"""Qt stylesheets: a modern flat theme in light and dark variants.

Design language: soft surfaces on a flat background, 8px rounded corners,
one violet accent, generous spacing, no bevels or gradients. Widgets opt into
roles via dynamic properties:

- QPushButton[variant="primary"]  - filled accent button (Generate)
- QPushButton[variant="danger"]   - destructive accent (Stop, delete)
- QLabel[chip="ok"|"warn"|"info"] - small rounded status chips
- QLabel[role="muted"]            - captions and helper text
- QFrame[card="true"]             - elevated surface card
- QToolButton[section="true"]     - collapsible section headers
"""

DARK = {
    'bg':      '#101216',
    'surface': '#181b21',
    'raised':  '#1f232b',
    'border':  '#2a2f3a',
    'text':    '#e7eaf0',
    'muted':   '#98a0af',
    'accent':  '#7c5cff',
    'accent_hover': '#8f73ff',
    'accent_press': '#6a4be0',
    'ok_bg':   '#173527',
    'ok_fg':   '#7ee2a8',
    'warn_bg': '#3a2f14',
    'warn_fg': '#ffd479',
    'info_bg': '#1c2a42',
    'info_fg': '#8ab8ff',
    'danger':  '#e05561',
    'sel_bg':  '#3a2f6e',
}

LIGHT = {
    'bg':      '#f4f5f8',
    'surface': '#ffffff',
    'raised':  '#f8f8fb',
    'border':  '#e2e4ea',
    'text':    '#1d2130',
    'muted':   '#69707f',
    'accent':  '#6246ea',
    'accent_hover': '#7257ff',
    'accent_press': '#5238cc',
    'ok_bg':   '#e3f6ea',
    'ok_fg':   '#1c7a44',
    'warn_bg': '#fdf3d8',
    'warn_fg': '#8a6410',
    'info_bg': '#e7efff',
    'info_fg': '#2c5cc5',
    'danger':  '#d63848',
    'sel_bg':  '#d9d2ff',
}


def build_qss(dark: bool) -> str:
    c = DARK if dark else LIGHT
    return f"""
* {{ outline: none; }}

QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; font-size: 10pt; }}

QLabel[role="muted"] {{ color: {c['muted']}; font-size: 8.5pt; }}
QLabel[role="title"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 10.5pt; font-weight: 600; }}

QLabel[chip] {{
    padding: 2px 10px; border-radius: 9px; font-size: 8.5pt; font-weight: 600;
}}
QLabel[chip="ok"]   {{ background: {c['ok_bg']};   color: {c['ok_fg']}; }}
QLabel[chip="warn"] {{ background: {c['warn_bg']}; color: {c['warn_fg']}; }}
QLabel[chip="info"] {{ background: {c['info_bg']}; color: {c['info_fg']}; }}

QFrame[card="true"] {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}

QPushButton {{
    background: {c['raised']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 14px;
}}
QPushButton:hover  {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}

QPushButton[variant="primary"] {{
    background: {c['accent']};
    border: none;
    color: white;
    font-weight: 600;
    padding: 9px 18px;
}}
QPushButton[variant="primary"]:hover   {{ background: {c['accent_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {c['accent_press']}; }}

QPushButton[variant="danger"] {{
    background: transparent; border: 1px solid {c['danger']}; color: {c['danger']};
}}
QPushButton[variant="danger"]:hover {{ background: {c['danger']}; color: white; }}

QPushButton[variant="ghost"] {{
    background: transparent; border: none; color: {c['muted']}; padding: 4px 8px;
}}
QPushButton[variant="ghost"]:hover {{ color: {c['text']}; }}

QToolButton[section="true"] {{
    background: transparent; border: none; text-align: left;
    font-weight: 600; padding: 7px 4px; font-size: 10pt;
}}
QToolButton[section="true"]:hover {{ color: {c['accent']}; }}

QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QComboBox {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {c['sel_bg']};
}}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus,
QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}

/* The chevron is painted by NoWheelComboBox.paintEvent (a real chevron that
   flips up when open); hide Qt's default arrow entirely. */
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; border: none; }}
QComboBox QAbstractItemView {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    selection-background-color: {c['sel_bg']}; padding: 4px;
}}

QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

QSlider::groove:horizontal {{
    height: 4px; background: {c['border']}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px; background: {c['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {c['border']}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}

QDockWidget {{ background: {c['bg']}; border: none; }}

QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['border']};
    background: {c['surface']};
}}
QCheckBox::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']};
}}

QMessageBox {{ background: {c['surface']}; }}
QToolTip {{
    background: {c['raised']}; color: {c['text']};
    border: 1px solid {c['border']}; padding: 5px; border-radius: 6px;
}}
"""
