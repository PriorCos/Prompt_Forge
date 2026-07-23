"""Prompt Forge - standalone edition (PySide6/Qt).

True native desktop app: no local web server. Two-pane main screen (compose
left, result right), settings in a slide-out left panel that keeps the
accordion design from the previous UI, debug log panel at the bottom.

All logic lives in pf/ (client, prompts catalog, cleanup, history, settings,
debug log) and is UI-agnostic; this file is only the Qt front end.

Run:  python app.py
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation,
                            QRect, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QPainter, QPen,
                           QPixmap, QShortcut, QKeySequence, QTextCursor)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDockWidget, QDoubleSpinBox,
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizeGrip, QSizePolicy, QSlider, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from pf import debuglog, history, models, presets, prompts, settings
from pf.cleanup import cleanup, join_undesired, split_undesired
from pf.nai_client import NAIClient, TokenExpired, make_params
from pf.prompts import enrich_message, refine_message, variation_message
from pf.qss import build_qss

prompts.ensure_files()
models.ensure_file()
presets.ensure_file()

# --- Windows frameless-window native support (resize borders + taskbar-aware
# maximize). No-ops on other platforms; the QSizeGrip still works there. ------
IS_WIN = sys.platform == 'win32'
RESIZE_BORDER = 7
_WM_NCHITTEST = 0x0084
_WM_GETMINMAXINFO = 0x0024
_HT = {  # (left, top, right, bottom) -> Windows hit-test code
    (True, True, False, False): 13, (False, True, False, False): 12,
    (False, True, True, False): 14, (True, False, False, False): 10,
    (False, False, True, False): 11, (True, False, False, True): 16,
    (False, False, False, True): 15, (False, False, True, True): 17,
}

if IS_WIN:
    class _POINT(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [('ptReserved', _POINT), ('ptMaxSize', _POINT),
                    ('ptMaxPosition', _POINT), ('ptMinTrackSize', _POINT),
                    ('ptMaxTrackSize', _POINT)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

cfg = settings.load()
debuglog.set_level(cfg['log_level'])

state = {
    'notes': '',
    'main': '',
    'undesired': '',
}
last_output = cfg.get('last_output', '')
state['main'], state['undesired'] = split_undesired(last_output)

gen = {'running': False, 'kind': '', 'family': 'tags',
       'notes': '', 'idea': '', 'result': None, 'error': None, 'thread': None}
cancel_event = threading.Event()
stream_parts: list[str] = []
expansion_open: dict[str, bool] = {}

def client() -> NAIClient:
    return NAIClient(cfg['token'], cfg['endpoint'])


def persist() -> None:
    cfg['last_output'] = last_output
    settings.save(cfg)


def active_family() -> str:
    return prompts.family_of(cfg['prompt'])


def selected_appends(family: str) -> list[str]:
    ids = {ap['id'] for ap in prompts.append_items(family)}
    return [a for a in cfg['active_appends'] if a in ids]


def system_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


def theme_is_dark() -> bool:
    return {'dark': True, 'light': False}.get(cfg['theme'], system_dark())


# Colour used for the combo-box chevron; kept in sync with the active theme so
# the painted chevron matches the (QSS-driven) muted text colour.
CHEVRON = {'color': '#98a0af'}


def apply_theme() -> None:
    from pf.qss import DARK, LIGHT
    dark = theme_is_dark()
    CHEVRON['color'] = (DARK if dark else LIGHT)['muted']
    QApplication.instance().setStyleSheet(build_qss(dark))


# --- animation governance ---------------------------------------------------
# Two user settings gate everything: `animations` is the master switch;
# `reduce_motion` keeps opacity fades (gentle) but drops spatial motion
# (slide / spin / height-expand) for motion-sensitive users.

def anim_on() -> bool:
    return bool(cfg.get('animations', True))


def motion_on() -> bool:
    """Whether spatial motion (movement, rotation, expand) is allowed."""
    return anim_on() and not bool(cfg.get('reduce_motion', False))


# All animation durations pass through here, so overall speed is one knob.
# >1 slows everything down proportionally.
ANIM_SCALE = 1.4


def dur(ms: int) -> int:
    return int(ms * ANIM_SCALE)


def fade(widget, start: float, end: float, ms: int = 160, on_done=None) -> None:
    """Fade a widget's opacity. Instant (with on_done) when animations are off.
    The temporary opacity effect is removed when the fade finishes."""
    if not anim_on() or widget is None:
        if on_done:
            on_done()
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b'opacity', widget)
    anim.setDuration(dur(ms))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(start)
    anim.setEndValue(end)

    def done() -> None:
        widget.setGraphicsEffect(None)
        if on_done:
            on_done()

    anim.finished.connect(done)
    widget._fade_anim = anim  # keep a reference so it isn't garbage-collected
    anim.start()


def mono_font(size: int = 9) -> QFont:
    f = QFont('Consolas', size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------

class NoWheelComboBox(QComboBox):
    """Ignores the scroll wheel while closed (so scrolling the settings panel
    never changes a value), and paints a real chevron that points down when
    closed and up when open - Qt's QSS arrow renders as an ugly box."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._angle = 0.0  # 0 = chevron down, 180 = up; animated between
        self._arrow_anim = QVariantAnimation(self)
        self._arrow_anim.setDuration(dur(160))
        self._arrow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._arrow_anim.valueChanged.connect(self._set_angle)

    def _set_angle(self, value) -> None:
        self._angle = float(value)
        self.update()

    def _spin_to(self, target: float) -> None:
        if not motion_on():  # reduce-motion / off: snap, no spin
            self._angle = target
            self.update()
            return
        self._arrow_anim.stop()
        self._arrow_anim.setStartValue(self._angle)
        self._arrow_anim.setEndValue(target)
        self._arrow_anim.start()

    def wheelEvent(self, event) -> None:
        event.ignore()

    def showPopup(self) -> None:
        self._spin_to(180.0)
        super().showPopup()

    def hidePopup(self) -> None:
        self._spin_to(0.0)
        super().hidePopup()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # frame + text; QSS suppresses the native arrow
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(CHEVRON['color']))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        s = 4.0
        painter.translate(self.width() - 15, self.height() / 2)
        painter.rotate(self._angle)  # 0 -> down chevron, 180 -> up
        painter.drawLine(QPointF(-s, -s / 2), QPointF(0, s / 2))
        painter.drawLine(QPointF(0, s / 2), QPointF(s, -s / 2))
        painter.end()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSlider(QSlider):
    def wheelEvent(self, event) -> None:
        event.ignore()


def glyph_icon(glyph: str, px: int = 22) -> QIcon:
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    f = QFont('Segoe UI Emoji', int(px * 0.55))
    painter.setFont(f)
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return QIcon(pm)


class SubmitTextEdit(QPlainTextEdit):
    """Multiline input whose configured key combo submits instead of newline."""

    submitted = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            want = cfg['submit_key']
            hit = ((want == 'enter' and mods == Qt.KeyboardModifier.NoModifier)
                   or (want == 'shift-enter' and mods == Qt.KeyboardModifier.ShiftModifier)
                   or (want == 'ctrl-enter' and mods == Qt.KeyboardModifier.ControlModifier))
            if hit:
                self.submitted.emit()
                return
        super().keyPressEvent(event)


class Collapsible(QWidget):
    """Accordion section: header QToolButton + collapsible body. Open state is
    remembered in `expansion_open` so rebuilds keep sections where you left them."""

    def __init__(self, title: str, start_open: bool = False):
        super().__init__()
        self._title = title
        is_open = expansion_open.get(title, start_open)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = QToolButton()
        self.header.setProperty('section', 'true')
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(is_open)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.DownArrow if is_open else Qt.ArrowType.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = QWidget()
        self.body.setVisible(is_open)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 2, 2, 10)
        self.body_layout.setSpacing(6)
        lay.addWidget(self.header)
        lay.addWidget(self.body)
        self._anim = QPropertyAnimation(self.body, b'maximumHeight', self)
        self._anim.setDuration(dur(180))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._anim_done)
        self._target_open = is_open
        self.header.toggled.connect(self._toggled)

    _MAX = 16777215  # QWIDGETSIZE_MAX - lets the body grow freely with content

    def _toggled(self, on: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        expansion_open[self._title] = on
        self._target_open = on
        self._anim.stop()
        if not motion_on():
            self.body.setMaximumHeight(self._MAX)
            self.body.setVisible(on)
            return

        if on:
            if self.body.isVisible():
                start = self.body.maximumHeight()  # mid-collapse: continue from here
            else:
                # clamp to 0 BEFORE showing so content never flashes at full height
                self.body.setMaximumHeight(0)
                self.body.setVisible(True)
                start = 0
            end = max(1, self.body.sizeHint().height())
        else:
            start = self.body.height() if self.body.isVisible() else 0
            end = 0

        self.body.setMaximumHeight(start)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _anim_done(self) -> None:
        if self._target_open:
            self.body.setMaximumHeight(self._MAX)  # uncap so content isn't clipped
        else:
            self.body.setVisible(False)
            self.body.setMaximumHeight(self._MAX)


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty('role', 'muted')
    lab.setWordWrap(True)
    return lab


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty('card', 'true')
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    return frame, lay


def flash(button: QPushButton, text: str = 'Copied ✓', ms: int = 1200) -> None:
    original = button.text()
    button.setText(text)
    fade(button, 0.4, 1.0, 220)  # brief pulse to confirm the action landed
    QTimer.singleShot(ms, lambda: button.setText(original))


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class ClickScrim(QWidget):
    """Transparent layer behind the settings panel; a click on it closes the
    panel. Sits below the header so the ☰ button keeps toggling normally."""

    def __init__(self, parent, on_click):
        super().__init__(parent)
        self._on_click = on_click

    def mousePressEvent(self, event) -> None:
        self._on_click()
        super().mousePressEvent(event)


class DragHeader(QWidget):
    """Custom title row for the frameless window: empty areas drag the window,
    double-click toggles maximize. Buttons inside keep working normally."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        win = self.window()
        if hasattr(win, 'toggle_max'):
            win.toggle_max()
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Prompt Forge')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self._dialogs: list[QDialog] = []
        self._log_seen = 0
        self._stream_len = 0
        self._programmatic = False
        self._settings_open = False
        self._max_anim = None  # geometry animation for maximize/restore

        self._build_central()
        self._build_settings_overlay()
        self._build_debug_dock()
        self._restore_geometry()

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(120)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()

        self._refresh_output_widgets()
        self.update_badge()
        debuglog.log('app started (Qt)', 'basic')

    # ------------------------------------------------------------ geometry --

    def _restore_geometry(self) -> None:
        w = cfg.get('window') or {}
        width, height = int(w.get('w', 1150)), int(w.get('h', 780))
        self.resize(max(700, width), max(500, height))
        if 'x' in w and 'y' in w:
            # only honour a saved position that still lands on a screen
            pt = QPoint(int(w['x']), int(w['y']))
            on_screen = any(scr.availableGeometry().contains(pt)
                            for scr in QGuiApplication.screens())
            if on_screen:
                self.move(pt)
        if w.get('maximized'):
            QTimer.singleShot(0, self.showMaximized)

    def _save_geometry(self) -> None:
        ng = self.normalGeometry()  # non-maximized size/pos, even if maximized now
        cfg['window'] = {'x': ng.x(), 'y': ng.y(), 'w': ng.width(), 'h': ng.height(),
                         'maximized': self.isMaximized()}
        persist()

    def _escape(self) -> None:
        if self._settings_open:
            self.toggle_settings()

    # ------------------------------------------------------------------ UI --

    def _build_central(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(10)

        # header = custom title bar: drag to move, hosts window controls
        self.header_bar = DragHeader()
        head = QHBoxLayout(self.header_bar)
        head.setContentsMargins(0, 0, 0, 0)
        burger = QPushButton('☰')
        burger.setProperty('variant', 'ghost')
        burger.setToolTip('Settings')
        burger.setFixedWidth(36)
        burger.clicked.connect(self.toggle_settings)
        title = QLabel('Prompt Forge')
        title.setProperty('role', 'title')
        self.mode_chip = QLabel('')
        self.mode_chip.setProperty('chip', 'info')
        hist_btn = QPushButton('History')
        hist_btn.setProperty('variant', 'ghost')
        hist_btn.clicked.connect(self.open_history)
        min_btn = QPushButton('–')
        min_btn.setProperty('variant', 'ghost')
        min_btn.setToolTip('Minimize')
        min_btn.setFixedWidth(32)
        min_btn.clicked.connect(self._minimize)
        self.max_btn = QPushButton('□')
        self.max_btn.setProperty('variant', 'ghost')
        self.max_btn.setToolTip('Maximize')
        self.max_btn.setFixedWidth(32)
        self.max_btn.clicked.connect(self.toggle_max)
        close_btn = QPushButton('✕')
        close_btn.setProperty('variant', 'ghost')
        close_btn.setToolTip('Close')
        close_btn.setFixedWidth(32)
        close_btn.clicked.connect(self.close)
        head.addWidget(burger)
        head.addSpacing(6)
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.mode_chip)
        head.addStretch(1)
        head.addWidget(hist_btn)
        head.addSpacing(10)
        head.addWidget(min_btn)
        head.addWidget(self.max_btn)
        head.addWidget(close_btn)
        root.addWidget(self.header_bar)

        panes = QHBoxLayout()
        panes.setSpacing(14)

        # left: compose card
        left_card, left = card()
        left_card.setFixedWidth(380)
        lab = QLabel('Idea')
        lab.setProperty('role', 'heading')
        left.addWidget(lab)
        self.idea_edit = SubmitTextEdit()
        self.idea_edit.setPlaceholderText(
            'she stops at the top of the stairwell, hand still on the rail, listening')
        self.idea_edit.setPlainText(cfg.get('idea', ''))
        self.idea_edit.setMinimumHeight(240)
        self.idea_edit.submitted.connect(lambda: self.do_run('fresh'))
        self.idea_edit.textChanged.connect(self._idea_changed)
        left.addWidget(self.idea_edit, 1)
        self.generate_btn = QPushButton('Generate')
        self.generate_btn.setProperty('variant', 'primary')
        self.generate_btn.clicked.connect(lambda: self.do_run('fresh'))
        left.addWidget(self.generate_btn)
        panes.addWidget(left_card)

        # right: result card
        right_card, right = card()
        head2 = QHBoxLayout()
        rlab = QLabel('Result')
        rlab.setProperty('role', 'heading')
        self.badge = QLabel('')
        self.badge.setProperty('chip', 'ok')
        self.badge.hide()
        self.stop_btn = QPushButton('⏹ Stop')
        self.stop_btn.setProperty('variant', 'danger')
        self.stop_btn.clicked.connect(self.stop_generation)
        self.stop_btn.hide()
        self.copy_btn = QPushButton('Copy')
        self.copy_btn.clicked.connect(self.copy_main)
        head2.addWidget(rlab)
        head2.addSpacing(8)
        head2.addWidget(self.badge)
        head2.addStretch(1)
        head2.addWidget(self.stop_btn)
        head2.addWidget(self.copy_btn)
        right.addLayout(head2)

        self.status = muted('Ready.')
        right.addWidget(self.status)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setFont(mono_font())
        self.output_edit.setPlaceholderText('The generated image prompt appears here. '
                                            'You can edit it directly - Refine continues from your edits.')
        self.output_edit.setMinimumHeight(220)
        self.output_edit.textChanged.connect(self._output_edited)
        right.addWidget(self.output_edit, 1)

        self.und_row = QWidget()
        und_lay = QHBoxLayout(self.und_row)
        und_lay.setContentsMargins(0, 0, 0, 0)
        und_head = QLabel('Undesired:')
        und_head.setProperty('role', 'heading')
        self.und_label = QLabel('')
        self.und_label.setFont(mono_font())
        self.und_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_und_btn = QPushButton('Copy')
        self.copy_und_btn.clicked.connect(self.copy_undesired)
        und_lay.addWidget(und_head)
        und_lay.addWidget(self.und_label, 1)
        und_lay.addWidget(self.copy_und_btn)
        self.und_row.hide()
        right.addWidget(self.und_row)

        nlab = QLabel('Notes')
        nlab.setProperty('role', 'heading')
        right.addWidget(nlab)
        self.notes_edit = SubmitTextEdit()
        self.notes_edit.setPlaceholderText('colder light, pull back to a wide shot / the background')
        self.notes_edit.setFixedHeight(64)
        self.notes_edit.submitted.connect(lambda: self.do_run('refine'))
        right.addWidget(self.notes_edit)

        actions = QHBoxLayout()
        self.action_buttons = [self.generate_btn]
        for text, kind, tip in (
                ('Refine', 'refine', 'Revise the prompt using the notes.'),
                ('Variation', 'variation', 'A different take on the same idea.'),
                ('Add detail', 'enrich',
                 'Invent hyper-specific new detail about whatever the notes name. '
                 'Unlike Refine, it may add new things.')):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kind: self.do_run(k))
            actions.addWidget(b)
            self.action_buttons.append(b)
        actions.addStretch(1)
        right.addLayout(actions)
        panes.addWidget(right_card, 1)

        root.addLayout(panes, 1)
        self.setCentralWidget(central)
        self._refresh_mode_chip()

    # ---------------------------------------------------- settings overlay --

    def _build_settings_overlay(self) -> None:
        """The settings panel floats OVER the main page (no push), anchored to
        the left edge below the header, so the header - and its button - never
        move when it opens. A scrim behind it (below the header, so the ☰
        button keeps working) closes the panel on an outside click."""
        self.scrim = ClickScrim(self.centralWidget(), self._escape)
        self.scrim.setStyleSheet('background-color: rgba(0, 0, 0, 45);')
        self.scrim.hide()
        self.scrim_effect = QGraphicsOpacityEffect(self.scrim)
        self.scrim_effect.setOpacity(0.0)
        self.scrim.setGraphicsEffect(self.scrim_effect)

        self.settings_panel = QFrame(self.centralWidget())
        self.settings_panel.setProperty('card', 'true')
        lay = QVBoxLayout(self.settings_panel)
        lay.setContentsMargins(4, 4, 4, 4)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        lay.addWidget(self.settings_scroll)
        self.settings_panel.hide()
        self.size_grip = QSizeGrip(self.centralWidget())

        # slide (panel position) + fade (scrim dim). Either finishing triggers
        # the close-hide, so it works whether or not spatial motion is enabled.
        self._slide_anim = QPropertyAnimation(self.settings_panel, b'pos', self)
        self._slide_anim.setDuration(dur(210))
        self._slide_anim.finished.connect(self._slide_done)
        self._scrim_anim = QPropertyAnimation(self.scrim_effect, b'opacity', self)
        self._scrim_anim.setDuration(dur(210))
        self._scrim_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scrim_anim.finished.connect(self._slide_done)
        self._rebuild_settings()

    def toggle_settings(self) -> None:
        opening = not self._settings_open
        self._settings_open = opening
        debuglog.log(f'settings panel {"opened" if opening else "closed"}', 'verbose')
        self._place_overlays()
        panel = self.settings_panel
        on_x, off_x, y = 6, -panel.width() - 16, panel.y()
        self._slide_anim.stop()
        self._scrim_anim.stop()

        if opening:
            panel.show()
            self.scrim.show()
            self.scrim.raise_()
            panel.raise_()
            self.size_grip.raise_()

        if not anim_on():  # instant
            panel.move(on_x if opening else off_x, y)
            self.scrim_effect.setOpacity(1.0 if opening else 0.0)
            self._slide_done()
            return

        # scrim always fades (gentle, allowed even in reduce-motion)
        self._scrim_anim.setStartValue(self.scrim_effect.opacity())
        self._scrim_anim.setEndValue(1.0 if opening else 0.0)
        self._scrim_anim.start()

        if motion_on():  # slide the panel
            start_x = off_x if opening else panel.x()
            panel.move(start_x, y)
            self._slide_anim.setEasingCurve(
                QEasingCurve.Type.OutCubic if opening else QEasingCurve.Type.InCubic)
            self._slide_anim.setStartValue(QPoint(start_x, y))
            self._slide_anim.setEndValue(QPoint(on_x if opening else off_x, y))
            self._slide_anim.start()
        else:  # reduce-motion: no slide, just place it (scrim still fades)
            panel.move(on_x if opening else off_x, y)

    def _slide_done(self) -> None:
        if not self._settings_open:  # finished a close - now hide the widgets
            self.settings_panel.hide()
            self.scrim.hide()

    def _place_overlays(self) -> None:
        central = self.centralWidget()
        if central is None or not hasattr(self, 'settings_panel'):
            return
        top = self.header_bar.y() + self.header_bar.height() + 8
        self.scrim.setGeometry(0, top, central.width(), max(0, central.height() - top))
        self.settings_panel.setGeometry(6, top, 372, max(120, central.height() - top - 6))
        grip = self.size_grip.sizeHint()
        self.size_grip.move(central.width() - grip.width(), central.height() - grip.height())
        self.size_grip.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_overlays()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_overlays()

    def _rebuild_settings(self) -> None:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(2)
        head = QLabel('Settings')
        head.setProperty('role', 'title')
        lay.addWidget(head)
        lay.addSpacing(6)

        lay.addWidget(self._section_generation())
        lay.addWidget(self._section_sampling())
        lay.addWidget(self._section_prompts())
        lay.addWidget(self._section_limits())
        lay.addWidget(self._section_input())
        lay.addWidget(self._section_appearance())
        lay.addWidget(self._section_connection())
        lay.addWidget(self._section_debug())
        lay.addWidget(self._section_motion())

        restore = QPushButton('Restore defaults')
        restore.setProperty('variant', 'danger')
        restore.clicked.connect(self.restore_defaults)
        lay.addSpacing(8)
        lay.addWidget(restore)
        lay.addStretch(1)
        self.settings_scroll.setWidget(panel)

    def _section_generation(self) -> QWidget:
        sec = Collapsible('Generation', start_open=True)
        opts = models.options()
        combo = NoWheelComboBox()
        for mid, name in opts.items():
            combo.addItem(name, mid)
        if cfg['model'] in opts:
            combo.setCurrentIndex(list(opts).index(cfg['model']))
        desc = muted(models.description(cfg['model']))

        def model_changed() -> None:
            cfg['model'] = combo.currentData()
            desc.setText(models.description(cfg['model']))
            persist()
            debuglog.log(f'setting model = {cfg["model"]}', 'verbose')

        combo.currentIndexChanged.connect(model_changed)
        sec.body_layout.addWidget(QLabel('Model'))
        sec.body_layout.addWidget(combo)
        sec.body_layout.addWidget(desc)

        for key, label, lo, hi, step, suffix in (
                ('tag_target', 'Tag target', 10, 60, 1, 'tags'),
                ('krea_words', 'Word target', 50, 300, 10, 'words')):
            value_label = QLabel(f'{label}: {int(cfg[key])} {suffix}')
            slider = NoWheelSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setSingleStep(step)
            slider.setValue(int(cfg[key]))

            def changed(v, k=key, vl=value_label, la=label, su=suffix) -> None:
                cfg[k] = int(v)
                vl.setText(f'{la}: {int(v)} {su}')
                persist()
                self.update_badge()

            slider.valueChanged.connect(changed)
            sec.body_layout.addSpacing(4)
            sec.body_layout.addWidget(value_label)
            sec.body_layout.addWidget(slider)
        return sec

    def _section_prompts(self) -> QWidget:
        self.prompts_section = Collapsible('Prompts', start_open=True)
        self._fill_prompts_section()
        return self.prompts_section

    def _fill_prompts_section(self) -> None:
        body = self.prompts_section.body_layout
        while body.count():
            item = body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        opts = prompts.system_options()
        if cfg['prompt'] not in opts:
            cfg['prompt'] = next(iter(opts))
        combo = NoWheelComboBox()
        for pid, name in opts.items():
            combo.addItem(name, pid)
        combo.setCurrentIndex(list(opts).index(cfg['prompt']))

        def prompt_changed() -> None:
            cfg['prompt'] = combo.currentData()
            persist()
            debuglog.log(f'prompt -> {cfg["prompt"]}', 'verbose')
            self.update_badge()
            self._refresh_mode_chip()
            self._fill_prompts_section()

        combo.currentIndexChanged.connect(prompt_changed)
        body.addWidget(QLabel('Generation mode'))
        body.addWidget(combo)
        family = active_family()

        body.addSpacing(6)
        heading = QLabel('Append prompts, top to bottom:')
        heading.setProperty('role', 'heading')
        body.addWidget(heading)

        all_appends = prompts.append_items(family)
        name_of = {ap['id']: ap['name'] for ap in all_appends}
        sel = selected_appends(family)
        if not sel:
            body.addWidget(muted('None selected.'))
        for idx, aid in enumerate(sel):
            row = QHBoxLayout()
            row.addWidget(QLabel(f'{idx + 1}.'))
            name_lab = QLabel(name_of.get(aid, aid))
            row.addWidget(name_lab, 1)
            for glyph, delta, enabled in (('↑', -1, idx > 0), ('↓', 1, idx < len(sel) - 1)):
                b = QPushButton(glyph)
                b.setProperty('variant', 'ghost')
                b.setFixedWidth(28)
                b.setEnabled(enabled)
                b.clicked.connect(lambda _=False, a=aid, d=delta: self._move_append(a, d))
                row.addWidget(b)
            x = QPushButton('✕')
            x.setProperty('variant', 'ghost')
            x.setFixedWidth(28)
            x.clicked.connect(lambda _=False, a=aid: self._remove_append(a))
            row.addWidget(x)
            wrap = QWidget()
            wrap.setLayout(row)
            body.addWidget(wrap)

        available = {ap['id']: ap['name'] for ap in all_appends if ap['id'] not in sel}
        if available:
            adder = NoWheelComboBox()
            adder.addItem('Add an append prompt...', '')
            for aid, name in available.items():
                adder.addItem(name, aid)

            def add_selected() -> None:
                aid = adder.currentData()
                if aid:
                    cfg['active_appends'] = cfg['active_appends'] + [aid]
                    persist()
                    self._fill_prompts_section()

            adder.currentIndexChanged.connect(add_selected)
            body.addWidget(adder)
        elif not all_appends:
            body.addWidget(muted('No append prompts exist for this output type yet - '
                                 'create one in the library.'))

        manage = QPushButton('Manage prompt library')
        manage.clicked.connect(self.open_prompt_manager)
        body.addSpacing(4)
        body.addWidget(manage)

    def _move_append(self, aid: str, delta: int) -> None:
        family = active_family()
        sel = selected_appends(family)
        i = sel.index(aid)
        j = i + delta
        if 0 <= j < len(sel):
            sel[i], sel[j] = sel[j], sel[i]
            others = [a for a in cfg['active_appends'] if a not in sel]
            cfg['active_appends'] = sel + others
            persist()
            self._fill_prompts_section()

    def _remove_append(self, aid: str) -> None:
        cfg['active_appends'] = [a for a in cfg['active_appends'] if a != aid]
        persist()
        self._fill_prompts_section()

    # ----------------------------------------------------------- sampling --

    def _section_sampling(self) -> QWidget:
        self.sampling_section = Collapsible('Sampling', start_open=False)
        self._fill_sampling()
        return self.sampling_section

    def _fill_sampling(self) -> None:
        body = self.sampling_section.body_layout
        clear_layout(body)

        opts = presets.options()
        if cfg['preset'] not in opts:
            cfg['preset'] = next(iter(opts))
        builtin = presets.is_builtin(cfg['preset'])

        combo = NoWheelComboBox()
        for pid, name in opts.items():
            combo.addItem(name, pid)
        combo.setCurrentIndex(list(opts).index(cfg['preset']))

        def preset_changed() -> None:
            cfg['preset'] = combo.currentData()
            persist()
            debuglog.log(f'preset -> {cfg["preset"]}', 'verbose')
            self._fill_sampling()

        combo.currentIndexChanged.connect(preset_changed)
        body.addWidget(QLabel('Active preset'))
        body.addWidget(combo)

        row = QHBoxLayout()
        new_btn = QPushButton('New')
        new_btn.clicked.connect(self._new_preset)
        dup_btn = QPushButton('Duplicate')
        dup_btn.clicked.connect(self._duplicate_preset)
        row.addWidget(new_btn)
        row.addWidget(dup_btn)
        if not builtin:
            del_btn = QPushButton('Delete')
            del_btn.setProperty('variant', 'danger')
            del_btn.clicked.connect(self._delete_preset)
            row.addWidget(del_btn)
        row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(row)
        body.addWidget(wrap)

        if builtin:
            body.addWidget(muted('Built-in preset is read-only. Use Duplicate to customize.'))

        params = presets.get_params(cfg['preset'])
        core = [p for p in presets.PARAMS if not p.get('advanced')]
        advanced = [p for p in presets.PARAMS if p.get('advanced')]
        for spec in core:
            body.addWidget(self._param_control(spec, params, enabled=not builtin))

        if advanced:
            adv = Collapsible('Advanced', start_open=False)
            for spec in advanced:
                adv.body_layout.addWidget(self._param_control(spec, params, enabled=not builtin))
            adv.body_layout.addWidget(muted("NovelAI's unified sampler. Leave at 0 / off unless "
                                           'you know what these do.'))
            body.addWidget(adv)

    def _param_control(self, spec: dict, params: dict, enabled: bool) -> QWidget:
        key = spec['key']
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        def commit(value) -> None:
            p = dict(presets.get_params(cfg['preset']))
            p[key] = value
            presets.update(cfg['preset'], params=p)
            debuglog.log(f'preset {cfg["preset"]}: {key}={value}', 'verbose')

        if spec['type'] == 'bool':
            box = QCheckBox(spec['label'])
            box.setChecked(bool(params[key]))
            box.setEnabled(enabled)
            if spec.get('hint'):
                box.setToolTip(spec['hint'])
            box.toggled.connect(commit)
            lay.addWidget(box)
            return wrap

        lay.addWidget(QLabel(spec['label']))
        if spec['type'] == 'float':
            ctrl = NoWheelDoubleSpinBox()
            ctrl.setDecimals(2)
            ctrl.setSingleStep(spec['step'])
            ctrl.setRange(spec['min'], spec['max'])
            ctrl.setValue(float(params[key]))
            ctrl.valueChanged.connect(commit)
        else:  # int
            ctrl = NoWheelSpinBox()
            ctrl.setSingleStep(spec['step'])
            ctrl.setRange(spec['min'], spec['max'])
            ctrl.setValue(int(params[key]))
            ctrl.valueChanged.connect(commit)
        ctrl.setEnabled(enabled)
        if spec.get('hint'):
            ctrl.setToolTip(spec['hint'])
        lay.addWidget(ctrl)
        return wrap

    def _new_preset(self) -> None:
        name, ok = QInputDialog.getText(self, 'New preset', 'Preset name:')
        if ok and name.strip():
            cfg['preset'] = presets.create(name.strip(), presets.default_params())
            persist()
            debuglog.log(f'preset created: {cfg["preset"]}', 'verbose')
            self._fill_sampling()

    def _duplicate_preset(self) -> None:
        base = presets.options().get(cfg['preset'], cfg['preset'])
        cfg['preset'] = presets.create(f'{base} copy', presets.get_params(cfg['preset']))
        persist()
        debuglog.log(f'preset duplicated -> {cfg["preset"]}', 'verbose')
        self._fill_sampling()

    def _delete_preset(self) -> None:
        if presets.delete(cfg['preset']):
            debuglog.log(f'preset deleted: {cfg["preset"]}', 'verbose')
            cfg['preset'] = next(iter(presets.options()))
            persist()
            self._fill_sampling()

    def _section_limits(self) -> QWidget:
        sec = Collapsible('Limits')
        for key, label in (('max_tokens_tags', 'Max tokens - tag mode'),
                           ('max_tokens_natural', 'Max tokens - natural language')):
            spin = NoWheelSpinBox()
            spin.setRange(150, 2000)
            spin.setValue(int(cfg[key]))
            spin.valueChanged.connect(lambda v, k=key: (cfg.__setitem__(k, int(v)), persist()))
            sec.body_layout.addWidget(QLabel(label))
            sec.body_layout.addWidget(spin)
        return sec

    def _section_input(self) -> QWidget:
        sec = Collapsible('Input')
        combo = NoWheelComboBox()
        for key, name in (('enter', 'Enter'),
                          ('shift-enter', 'Shift+Enter'),
                          ('ctrl-enter', 'Ctrl+Enter')):
            combo.addItem(name, key)
        combo.setCurrentIndex(['enter', 'shift-enter', 'ctrl-enter'].index(cfg['submit_key']))

        def changed() -> None:
            cfg['submit_key'] = combo.currentData()
            persist()

        combo.currentIndexChanged.connect(changed)
        sec.body_layout.addWidget(QLabel('Send generation with'))
        sec.body_layout.addWidget(combo)
        return sec

    def _section_appearance(self) -> QWidget:
        sec = Collapsible('Appearance')
        combo = NoWheelComboBox()
        for key, name in (('auto', 'Auto'), ('light', 'Light'), ('dark', 'Dark')):
            combo.addItem(name, key)
        combo.setCurrentIndex(['auto', 'light', 'dark'].index(cfg['theme']))

        def changed() -> None:
            cfg['theme'] = combo.currentData()
            persist()
            apply_theme()
            self._pulse_theme()

        combo.currentIndexChanged.connect(changed)
        sec.body_layout.addWidget(QLabel('Theme'))
        sec.body_layout.addWidget(combo)
        return sec

    def _section_connection(self) -> QWidget:
        sec = Collapsible('Connection')
        token = QLineEdit(cfg['token'])
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.textChanged.connect(lambda t: (cfg.__setitem__('token', t.strip()), persist()))
        # reveal toggle as an eye overlaying the right edge of the field
        eye = token.addAction(glyph_icon('👁'), QLineEdit.ActionPosition.TrailingPosition)
        eye.setToolTip('Show token')

        def toggle_token_visibility() -> None:
            hidden = token.echoMode() == QLineEdit.EchoMode.Password
            token.setEchoMode(QLineEdit.EchoMode.Normal if hidden
                              else QLineEdit.EchoMode.Password)
            eye.setIcon(glyph_icon('🙈' if hidden else '👁'))
            eye.setToolTip('Hide token' if hidden else 'Show token')

        eye.triggered.connect(toggle_token_visibility)
        endpoint = QLineEdit(cfg['endpoint'])
        endpoint.setPlaceholderText('empty = confirmed default endpoint')
        endpoint.textChanged.connect(lambda t: (cfg.__setitem__('endpoint', t.strip()), persist()))
        probe_btn = QPushButton('Probe endpoints')
        self.probe_out = QPlainTextEdit()
        self.probe_out.setReadOnly(True)
        self.probe_out.setFont(mono_font(8))
        self.probe_out.setFixedHeight(110)
        probe_btn.clicked.connect(self.probe_endpoints)
        sec.body_layout.addWidget(QLabel('Persistent API token'))
        sec.body_layout.addWidget(token)
        sec.body_layout.addWidget(muted('NovelAI account settings -> Get Persistent API Token. '
                                        'Expires roughly monthly.'))
        sec.body_layout.addWidget(QLabel('Endpoint override'))
        sec.body_layout.addWidget(endpoint)
        sec.body_layout.addWidget(probe_btn)
        sec.body_layout.addWidget(self.probe_out)
        return sec

    def _section_debug(self) -> QWidget:
        sec = Collapsible('Debug')
        combo = NoWheelComboBox()
        for key, name in (('off', 'Off'), ('basic', 'Basic'), ('verbose', 'Verbose')):
            combo.addItem(name, key)
        combo.setCurrentIndex(['off', 'basic', 'verbose'].index(cfg['log_level']))

        def changed() -> None:
            cfg['log_level'] = combo.currentData()
            persist()
            debuglog.set_level(cfg['log_level'])
            debuglog.log(f'logging set to {cfg["log_level"]}', 'basic')
            self.debug_dock.setVisible(cfg['log_level'] != 'off')

        combo.currentIndexChanged.connect(changed)
        sec.body_layout.addWidget(QLabel('Logging'))
        sec.body_layout.addWidget(combo)
        sec.body_layout.addWidget(muted('Basic logs each step; Verbose also saves full '
                                        'request/response payloads to the logs/ folder.'))
        return sec

    def _section_motion(self) -> QWidget:
        sec = Collapsible('Motion')
        disable_box = QCheckBox('Disable animations')
        disable_box.setChecked(not cfg['animations'])
        reduce_box = QCheckBox('Reduce motion')
        reduce_box.setChecked(bool(cfg['reduce_motion']))
        reduce_box.setEnabled(bool(cfg['animations']))
        reduce_box.setToolTip('Keep gentle fades but drop sliding, spinning and '
                              'expanding motion.')

        def disable_changed(off: bool) -> None:
            cfg['animations'] = not off
            persist()
            reduce_box.setEnabled(not off)

        def reduce_changed(on: bool) -> None:
            cfg['reduce_motion'] = bool(on)
            persist()

        disable_box.toggled.connect(disable_changed)
        reduce_box.toggled.connect(reduce_changed)
        sec.body_layout.addWidget(disable_box)
        sec.body_layout.addWidget(reduce_box)
        return sec

    # ---------------------------------------------------------- debug dock --

    def _build_debug_dock(self) -> None:
        self.debug_dock = QDockWidget('', self)
        self.debug_dock.setTitleBarWidget(QWidget())
        self.debug_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 4, 18, 8)
        bar = QHBoxLayout()
        lab = QLabel('Debug log')
        lab.setProperty('role', 'heading')
        open_btn = QPushButton('Open logs folder')
        open_btn.setProperty('variant', 'ghost')
        open_btn.clicked.connect(self.open_logs_folder)
        clear_btn = QPushButton('Clear')
        clear_btn.setProperty('variant', 'ghost')
        clear_btn.clicked.connect(self.clear_log)
        bar.addWidget(lab)
        bar.addStretch(1)
        bar.addWidget(open_btn)
        bar.addWidget(clear_btn)
        lay.addLayout(bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(mono_font(8))
        self.log_view.setFixedHeight(150)
        lay.addWidget(self.log_view)
        self.debug_dock.setWidget(inner)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.debug_dock)
        self.debug_dock.setVisible(cfg['log_level'] != 'off')

    def clear_log(self) -> None:
        debuglog.clear()
        self._log_seen = 0
        self.log_view.clear()
        debuglog.log('log cleared', 'basic')

    def open_logs_folder(self) -> None:
        debuglog.LOG_DIR.mkdir(exist_ok=True)
        try:
            os.startfile(str(debuglog.LOG_DIR))
        except OSError as e:
            QMessageBox.warning(self, 'Prompt Forge', f'Could not open folder: {e}')

    # ---------------------------------------------------------- generation --

    def do_run(self, kind: str) -> None:
        if gen['running']:
            self._status('Already running.')
            return
        idea = self.idea_edit.toPlainText().strip()
        if not idea:
            self._status('Write an idea first.')
            return
        if not cfg['token']:
            self._status('No API token - open Settings and paste one.')
            return
        if kind != 'fresh' and not last_output:
            self._status('Nothing to iterate on yet - Generate first.')
            return

        family = active_family()
        notes = self.notes_edit.toPlainText().strip()
        debuglog.log(f'{kind}: prompt={cfg["prompt"]} family={family} model={cfg["model"]} '
                     f'appends={selected_appends(family)}', 'basic')
        system = prompts.build_system(cfg['prompt'], cfg['active_appends'],
                                      {'tag_target': int(cfg['tag_target']),
                                       'word_target': int(cfg['krea_words'])})
        debuglog.log('system prompt built', 'verbose', system)
        messages = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': 'Idea:\n' + idea}]
        if kind != 'fresh':
            messages.append({'role': 'assistant', 'content': last_output})
            followup = {'variation': variation_message,
                        'enrich': enrich_message}.get(kind, refine_message)
            messages.append({'role': 'user', 'content': followup(notes)})

        sampling = dict(presets.get_params(cfg['preset']))
        if kind == 'variation':  # variation's job is divergence - nudge temp up
            sampling['temperature'] = min(sampling['temperature'] + 0.2, 2.0)
        params = make_params(
            cfg['model'],
            int(cfg['max_tokens_natural'] if family == 'natural' else cfg['max_tokens_tags']),
            sampling,
        )
        debuglog.log(f'preset={cfg["preset"]} temp={params.temperature} top_p={params.top_p} '
                     f'top_k={params.top_k} min_p={params.min_p} '
                     f'freq={params.frequency_penalty} pres={params.presence_penalty} '
                     f'seed={params.seed}', 'verbose')

        gen.update(running=True, kind=kind, family=family,
                   notes=notes, idea=idea, result=None, error=None)
        cancel_event.clear()
        stream_parts.clear()
        self._stream_len = 0
        self._set_output('')
        self._status({'variation': 'Rerolling...', 'refine': 'Refining...',
                             'enrich': 'Adding detail...'}.get(kind, 'Building...'))
        self.stop_btn.show()
        for b in self.action_buttons:
            b.setEnabled(False)

        def worker() -> None:
            try:
                gen['result'] = client().chat(messages, params,
                                              on_delta=stream_parts.append,
                                              cancel=cancel_event)
            except Exception as e:  # noqa: BLE001 - handed to the UI thread
                gen['error'] = e
                debuglog.exc('generation worker', 'basic')

        debuglog.log(f'worker thread started for {kind}', 'verbose')
        gen['thread'] = threading.Thread(target=worker, daemon=True)
        gen['thread'].start()

    def _poll(self) -> None:
        try:
            # stream text into the output box as it arrives, appending only the
            # new delta so scroll position holds; follow the end only when the
            # view is already at the bottom (so scrolling up to read stays put).
            if gen['running'] and len(stream_parts) > self._stream_len:
                new = ''.join(stream_parts[self._stream_len:])
                self._stream_len = len(stream_parts)
                sb = self.output_edit.verticalScrollBar()
                at_bottom = sb.value() >= sb.maximum() - 4
                self._programmatic = True
                cur = QTextCursor(self.output_edit.document())
                cur.movePosition(QTextCursor.MoveOperation.End)
                cur.insertText(new)
                self._programmatic = False
                if at_bottom:
                    sb.setValue(sb.maximum())
            if gen['running'] and gen['thread'] is not None and not gen['thread'].is_alive():
                self._finalize()
        except Exception:  # noqa: BLE001 - a poll error must not kill the timer
            debuglog.exc('poll', 'basic')
        # debug log lines -> panel
        buf = debuglog.lines
        if len(buf) > self._log_seen:
            self.log_view.appendPlainText('\n'.join(buf[self._log_seen:]))
            self._log_seen = len(buf)

    def _finalize(self) -> None:
        global last_output
        gen['running'] = False
        self.stop_btn.hide()
        for b in self.action_buttons:
            b.setEnabled(True)
        error = gen['error']
        if error is not None:
            if isinstance(error, TokenExpired):
                debuglog.log('token rejected (401)', 'basic')
                self._status('Token rejected (401). Grab a fresh persistent token '
                                    'in NovelAI account settings.')
            else:
                debuglog.log(f'error: {type(error).__name__}: {error}', 'basic')
                self._status(f'Failed: {error}')
            return

        result = gen['result']
        text = cleanup(result.text, gen['family'])  # cleanup logs its own detail
        if not text:
            debuglog.log('empty response after cleanup', 'basic')
            self._status('Empty response, try again.')
            return
        last_output = text
        state['main'], state['undesired'] = split_undesired(text)
        self._refresh_output_widgets()
        fade(self.output_edit, 0.0, 1.0, 220)  # settle the finished result in
        tokens = result.usage.get('completion_tokens')
        stats = f'{result.model} · {result.seconds:.1f}s' \
                + (f' · {tokens} tok' if tokens else '')
        done = 'Stopped early - partial result.' if result.stopped else \
            ('Tags ready.' if gen['family'] == 'tags' else 'Natural language prompt ready.')
        self._status(f'{done}  ·  {stats}')
        debuglog.log(f'finalized: family={gen["family"]} undesired={bool(state["undesired"])} '
                     f'usage={result.usage}', 'verbose')
        debuglog.log(f'done: {stats}', 'basic')
        if gen['kind'] != 'fresh' and gen['notes']:
            self.notes_edit.clear()
        cfg['idea'] = gen['idea']
        persist()
        try:
            history.append({'prompt': cfg['prompt'], 'family': gen['family'],
                            'model': cfg['model'], 'kind': gen['kind'],
                            'idea': gen['idea'], 'notes': gen['notes'], 'output': text})
            debuglog.log('history entry saved', 'verbose')
        except OSError:
            debuglog.exc('history append', 'basic')

    def stop_generation(self) -> None:
        cancel_event.set()
        debuglog.log('stop requested', 'basic')
        self._status('Stopping...')

    # ------------------------------------------------------- output helpers --

    def _set_output(self, text: str) -> None:
        self._programmatic = True
        self.output_edit.setPlainText(text)
        self._programmatic = False

    def _refresh_output_widgets(self) -> None:
        self._set_output(state['main'])
        self.und_label.setText(state['undesired'])
        self.und_row.setVisible(bool(state['undesired']))
        self.update_badge()

    def _output_edited(self) -> None:
        global last_output
        if self._programmatic or gen['running']:
            return
        state['main'] = self.output_edit.toPlainText()
        last_output = join_undesired(state['main'], state['undesired'])
        self.update_badge()

    def _idea_changed(self) -> None:
        cfg['idea'] = self.idea_edit.toPlainText()

    def update_badge(self) -> None:
        main = state['main'].strip()
        if not main:
            self.badge.hide()
            return
        if active_family() == 'tags':
            n = len([t for t in main.replace('|', ',').split(',') if t.strip()])
            target = int(cfg['tag_target'])
            self.badge.setText(f'{n} tags · target {target}')
        else:
            n = len(main.split())
            target = int(cfg['krea_words'])
            self.badge.setText(f'{n} words · target {target}')
        off = n > target * 1.3 or n < target * 0.4
        self.badge.setProperty('chip', 'warn' if off else 'ok')
        repolish(self.badge)
        self.badge.show()

    def _refresh_mode_chip(self) -> None:
        self.mode_chip.setText(prompts.system_options().get(cfg['prompt'], cfg['prompt']))

    def _status(self, text: str) -> None:
        """Set the status line with a quick fade-in so changes read as a
        cross-fade rather than a flicker."""
        self.status.setText(text)
        fade(self.status, 0.25, 1.0, 140)

    def _pulse_theme(self) -> None:
        """Soft window-opacity dip when the theme swaps, so it doesn't hard-cut."""
        if not anim_on():
            return
        anim = QPropertyAnimation(self, b'windowOpacity', self)
        anim.setDuration(dur(240))
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.5, 0.72)
        anim.setKeyValueAt(1.0, 1.0)
        self._theme_anim = anim  # keep a reference
        anim.start()

    def copy_main(self) -> None:
        if not state['main'].strip():
            self._status('Nothing to copy yet.')
            return
        QApplication.clipboard().setText(state['main'].strip())
        flash(self.copy_btn)

    def copy_undesired(self) -> None:
        if state['undesired'].strip():
            QApplication.clipboard().setText(state['undesired'].strip())
            flash(self.copy_und_btn)

    # -------------------------------------------------------------- probing --

    def probe_endpoints(self) -> None:
        if not cfg['token']:
            self.probe_out.setPlainText('No API token - paste one above first.')
            return
        self.probe_out.setPlainText('Probing...')
        debuglog.log(f'probe endpoints, model={cfg["model"]}', 'basic')

        def worker() -> None:
            try:
                results = client().probe(cfg['model'])
            except Exception as e:  # noqa: BLE001
                results = [{'error': str(e)}]
            gen['probe_results'] = results

        def check() -> None:
            if 'probe_results' in gen:
                results = gen.pop('probe_results')
                debuglog.log('probe results', 'verbose', results)
                self.probe_out.setPlainText('\n'.join(str(r) for r in results))
            else:
                QTimer.singleShot(200, check)

        threading.Thread(target=worker, daemon=True).start()
        QTimer.singleShot(200, check)

    # -------------------------------------------------------------- history --

    def open_history(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle('History')
        dlg.resize(680, 480)
        lay = QVBoxLayout(dlg)
        title = QLabel('History')
        title.setProperty('role', 'title')
        lay.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        ilay = QVBoxLayout(inner)
        entries = history.load()
        if not entries:
            ilay.addWidget(muted('Nothing here yet - every generation is saved automatically.'))
        for entry in entries:
            row = QHBoxLayout()
            row.addWidget(muted(entry['ts'][5:16].replace('T', ' ')))
            fam = entry.get('family') or ('natural' if entry.get('mode') == 'krea' else 'tags')
            chip = QLabel('NL' if fam == 'natural' else 'tags')
            chip.setProperty('chip', 'info')
            chip.setToolTip(entry.get('prompt', ''))
            row.addWidget(chip)
            idea_lab = QLabel((entry.get('idea') or '')[:60])
            row.addWidget(idea_lab, 1)
            load = QPushButton('Load')
            load.clicked.connect(lambda _=False, e=entry, d=dlg: self._load_history(e, d))
            row.addWidget(load)
            rm = QPushButton('✕')
            rm.setProperty('variant', 'ghost')
            rm.setFixedWidth(28)
            rm.clicked.connect(lambda _=False, e=entry, d=dlg: (
                history.delete(e.get('id', '')), d.close(), self.open_history()))
            row.addWidget(rm)
            wrap = QWidget()
            wrap.setLayout(row)
            ilay.addWidget(wrap)
        ilay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        self._dialogs.append(dlg)
        dlg.show()
        fade(dlg, 0.0, 1.0, 150)

    def _load_history(self, entry: dict, dlg: QDialog) -> None:
        global last_output
        cfg['idea'] = entry.get('idea', '')
        self.idea_edit.setPlainText(cfg['idea'])
        last_output = entry.get('output', '')
        state['main'], state['undesired'] = split_undesired(last_output)
        prompt_id = entry.get('prompt') or ('krea' if entry.get('mode') == 'krea' else 'nai')
        if prompt_id in prompts.system_options():
            cfg['prompt'] = prompt_id
            self._fill_prompts_section()
            self._refresh_mode_chip()
        self._refresh_output_widgets()
        persist()
        dlg.close()
        self._status('Loaded from history - Refine/Variation/Add detail continue from it.')

    # ------------------------------------------------------- prompt library --

    def open_prompt_manager(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle('Prompt library')
        dlg.resize(700, 520)
        lay = QVBoxLayout(dlg)
        title = QLabel('Prompt library')
        title.setProperty('role', 'title')
        lay.addWidget(title)
        lay.addWidget(muted('Full prompts are the instructions sent in place of the built-ins. '
                            'Append prompts are added at the end. Copy exports a prompt; '
                            'paste into a new one to import.'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        ilay = QVBoxLayout(inner)
        cat = prompts.load_catalog()
        for kind, heading in (('system', 'Full prompts'),
                              ('appends', 'Append prompts')):
            hl = QLabel(heading)
            hl.setProperty('role', 'heading')
            ilay.addWidget(hl)
            for e in cat[kind]:
                row = QHBoxLayout()
                row.addWidget(QLabel(e['name']), 1)
                fam = muted(e.get('family') or 'any')
                row.addWidget(fam)
                if e.get('builtin'):
                    row.addWidget(muted('built-in'))
                edit = QPushButton('Edit')
                edit.clicked.connect(lambda _=False, k=kind, i=e['id'], d=dlg:
                                     self.open_prompt_editor(k, i, d))
                row.addWidget(edit)
                copy = QPushButton('Copy')
                copy.clicked.connect(lambda _=False, k=kind, i=e['id'], b=copy: (
                    QApplication.clipboard().setText(prompts.entry_text(k, i)), flash(b)))
                row.addWidget(copy)
                if e.get('builtin'):
                    reset = QPushButton('Reset')
                    reset.clicked.connect(lambda _=False, k=kind, i=e['id']: (
                        prompts.reset_builtin(k, i),
                        self._status('Reset to the shipped default.')))
                    row.addWidget(reset)
                else:
                    rm = QPushButton('Delete')
                    rm.setProperty('variant', 'danger')
                    rm.clicked.connect(lambda _=False, k=kind, i=e['id'], d=dlg:
                                       self._delete_prompt(k, i, d))
                    row.addWidget(rm)
                wrap = QWidget()
                wrap.setLayout(row)
                ilay.addWidget(wrap)
        ilay.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        btns = QHBoxLayout()
        new_full = QPushButton('New full prompt')
        new_full.clicked.connect(lambda: self.open_prompt_editor('system', None, dlg))
        new_ap = QPushButton('New append prompt')
        new_ap.clicked.connect(lambda: self.open_prompt_editor('appends', None, dlg))
        close = QPushButton('Close')
        close.clicked.connect(dlg.close)
        btns.addWidget(new_full)
        btns.addWidget(new_ap)
        btns.addStretch(1)
        btns.addWidget(close)
        lay.addLayout(btns)
        self._dialogs.append(dlg)
        dlg.show()
        fade(dlg, 0.0, 1.0, 150)

    def _delete_prompt(self, kind: str, entry_id: str, dlg: QDialog) -> None:
        if not prompts.delete_entry(kind, entry_id):
            QMessageBox.information(self, 'Prompt Forge',
                                    'Built-in prompts cannot be deleted - use Reset.')
            return
        if kind == 'appends' and entry_id in cfg['active_appends']:
            cfg['active_appends'] = [a for a in cfg['active_appends'] if a != entry_id]
        persist()
        self._fill_prompts_section()
        self._refresh_mode_chip()
        self.update_badge()
        dlg.close()
        self.open_prompt_manager()

    def open_prompt_editor(self, kind: str, entry_id: str | None, parent_dlg: QDialog) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle('Edit prompt' if entry_id else 'New prompt')
        dlg.resize(700, 560)
        lay = QVBoxLayout(dlg)
        entry = prompts.find_entry(kind, entry_id) if entry_id else None
        title = QLabel(('Edit ' if entry_id else 'New ')
                       + ('full prompt' if kind == 'system' else 'append prompt'))
        title.setProperty('role', 'title')
        lay.addWidget(title)
        name = QLineEdit(entry['name'] if entry else '')
        name.setPlaceholderText('Name')
        lay.addWidget(QLabel('Name'))
        lay.addWidget(name)
        family = NoWheelComboBox()
        fam_opts = [('tags', 'Tags'), ('natural', 'Natural language')]
        if kind == 'appends':
            fam_opts.append(('any', 'Any output'))
        for key, label in fam_opts:
            family.addItem(label, key)
        current_fam = (entry.get('family') or 'any') if entry \
            else ('tags' if kind == 'system' else 'any')
        keys = [k for k, _ in fam_opts]
        family.setCurrentIndex(keys.index(current_fam) if current_fam in keys else 0)
        lay.addWidget(QLabel('Output family'))
        lay.addWidget(family)
        lay.addWidget(muted('Use $tag_target / $word_target for the length goal, and $appends '
                            'where append prompts should be inserted.' if kind == 'system'
                            else 'This text is added to the end of the instructions '
                            'when the append is active.'))
        text = QPlainTextEdit(prompts.entry_text(kind, entry_id) if entry_id else '')
        text.setFont(mono_font(8))
        lay.addWidget(text, 1)
        btns = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(dlg.close)
        save = QPushButton('Save')
        save.setProperty('variant', 'primary')

        def do_save() -> None:
            if not name.text().strip():
                QMessageBox.warning(self, 'Prompt Forge', 'Give the prompt a name.')
                return
            fam = family.currentData()
            fam = None if fam == 'any' else fam
            try:
                if entry_id:
                    prompts.update_entry(kind, entry_id, name=name.text(),
                                         text=text.toPlainText(), family=fam)
                    debuglog.log(f'prompt updated: {kind}/{entry_id}', 'verbose')
                else:
                    new_id = prompts.create_entry(kind, name.text(), text.toPlainText(),
                                                  family=fam or 'natural')
                    debuglog.log(f'prompt created: {kind}/{new_id}', 'verbose')
            except OSError:
                debuglog.exc('save prompt', 'basic')
                QMessageBox.warning(self, 'Prompt Forge', 'Could not save the prompt file.')
                return
            dlg.close()
            parent_dlg.close()
            self._fill_prompts_section()
            self._refresh_mode_chip()
            self.open_prompt_manager()

        save.clicked.connect(do_save)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)
        self._dialogs.append(dlg)
        dlg.show()
        fade(dlg, 0.0, 1.0, 150)

    # ------------------------------------------------------------- restore --

    def restore_defaults(self) -> None:
        answer = QMessageBox.question(
            self, 'Prompt Forge',
            'Restore all settings to defaults?\n\n'
            'Your token, endpoint, idea and current output are kept.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        keep = {k: cfg[k] for k in ('token', 'endpoint', 'idea', 'last_output', 'prompt')}
        cfg.update(settings.DEFAULTS)
        cfg.update(keep)
        persist()
        debuglog.set_level(cfg['log_level'])
        apply_theme()
        self._rebuild_settings()
        self._refresh_mode_chip()
        self.update_badge()
        self.debug_dock.setVisible(cfg['log_level'] != 'off')
        debuglog.log('settings restored to defaults', 'basic')
        self._status('Settings restored to defaults. Token and idea kept.')

    def toggle_max(self) -> None:
        self._restore_window() if self.isMaximized() else self._maximize_window()

    def _maximize_window(self) -> None:
        # Animate up to the work area, then commit the real OS maximized state
        # (WM_GETMINMAXINFO pins OS-maximize to the work area, so no final jump).
        if not motion_on():
            self.showMaximized()
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        self._animate_geometry(self.geometry(), screen.availableGeometry(),
                               self.showMaximized)

    def _restore_window(self) -> None:
        if not motion_on():
            self.showNormal()
            return
        target = self.normalGeometry()   # OS-remembered pre-maximize rect
        current = self.geometry()
        self.showNormal()                # leave maximized state...
        self.setGeometry(current)        # ...but hold the big size, then shrink
        self._animate_geometry(current, target, None)

    def _animate_geometry(self, start, end, on_done) -> None:
        if self._max_anim is not None:
            self._max_anim.stop()
        anim = QPropertyAnimation(self, b'geometry', self)
        anim.setDuration(dur(200))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(QRect(start))
        anim.setEndValue(QRect(end))
        anim.valueChanged.connect(lambda _: self._place_overlays())
        anim.finished.connect(self._place_overlays)
        if on_done is not None:
            anim.finished.connect(on_done)
        self._max_anim = anim
        anim.start()

    def _minimize(self) -> None:
        if not anim_on():
            self.showMinimized()
            return
        anim = QPropertyAnimation(self, b'windowOpacity', self)
        anim.setDuration(dur(150))
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self._after_min_fade)
        self._min_anim = anim  # keep a reference
        anim.start()

    def _after_min_fade(self) -> None:
        self.showMinimized()
        self.setWindowOpacity(1.0)  # visible again when restored from the taskbar

    def changeEvent(self, event) -> None:
        # OS maximized state is the single source of truth, so Win+Up, edge
        # snap, double-click and the button all sync the glyph / grip here.
        if event.type() == event.Type.WindowStateChange and hasattr(self, 'max_btn'):
            maxed = self.isMaximized()
            self.max_btn.setText('❐' if maxed else '□')
            self.max_btn.setToolTip('Restore' if maxed else 'Maximize')
            self.size_grip.setVisible(not maxed)
            self._place_overlays()
        super().changeEvent(event)

    def nativeEvent(self, eventType, message):
        if IS_WIN and eventType in (b'windows_generic_MSG', 'windows_generic_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_GETMINMAXINFO:
                # constrain "maximized" to the monitor work area so a frameless
                # window doesn't cover the taskbar
                self._fill_minmaxinfo(msg.lParam)
                return True, 0
            if msg.message == _WM_NCHITTEST and not self.isMaximized():
                x = ctypes.c_int16(msg.lParam & 0xFFFF).value
                y = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
                pt = self.mapFromGlobal(QPoint(x, y))  # screen -> window-local
                w, h, m = self.width(), self.height(), RESIZE_BORDER
                edges = (pt.x() < m, pt.y() < m, pt.x() > w - m, pt.y() > h - m)
                code = _HT.get(edges)
                if code is not None:
                    return True, code
        return super().nativeEvent(eventType, message)

    def _fill_minmaxinfo(self, lparam) -> None:
        try:
            info = _MINMAXINFO.from_address(int(lparam))
            hwnd = int(self.winId())
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)  # NEAREST
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi))
            work, mon = mi.rcWork, mi.rcMonitor
            info.ptMaxSize.x = work.right - work.left
            info.ptMaxSize.y = work.bottom - work.top
            info.ptMaxPosition.x = work.left - mon.left
            info.ptMaxPosition.y = work.top - mon.top
            info.ptMaxTrackSize.x = work.right - work.left
            info.ptMaxTrackSize.y = work.bottom - work.top
        except (OSError, ValueError):
            pass

    def closeEvent(self, event) -> None:
        self._save_geometry()
        super().closeEvent(event)


def _install_error_capture() -> None:
    """Route uncaught Python exceptions and Qt runtime messages into the log,
    so nothing fails silently once logging is on."""
    prev_hook = sys.excepthook

    def hook(exc_type, exc_value, tb):
        import traceback as _tb
        debuglog.log(f'UNCAUGHT {exc_type.__name__}: {exc_value}', 'basic')
        debuglog.log('uncaught traceback', 'verbose',
                     ''.join(_tb.format_exception(exc_type, exc_value, tb)).rstrip())
        prev_hook(exc_type, exc_value, tb)

    sys.excepthook = hook

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    sev = {QtMsgType.QtDebugMsg: 'verbose', QtMsgType.QtInfoMsg: 'verbose',
           QtMsgType.QtWarningMsg: 'basic', QtMsgType.QtCriticalMsg: 'basic',
           QtMsgType.QtFatalMsg: 'basic'}

    def qt_handler(mode, context, message):
        debuglog.log(f'Qt: {message}', sev.get(mode, 'verbose'))

    qInstallMessageHandler(qt_handler)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName('Prompt Forge')
    app.setFont(QFont('Segoe UI', 10))
    _install_error_capture()
    apply_theme()
    win = MainWindow()
    if anim_on():  # gentle fade-in on launch
        win.setWindowOpacity(0.0)
        win.show()
        launch = QPropertyAnimation(win, b'windowOpacity', win)
        launch.setDuration(dur(240))
        launch.setStartValue(0.0)
        launch.setEndValue(1.0)
        launch.start()
        win._launch_anim = launch  # keep a reference
    else:
        win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
