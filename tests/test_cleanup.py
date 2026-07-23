"""Offline tests for the ported post-processing. Run: python -m unittest discover tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pf.cleanup import cleanup, join_undesired, split_undesired  # noqa: E402
from pf import prompts  # noqa: E402


class CleanupTags(unittest.TestCase):
    def test_strips_end_and_after(self):
        self.assertEqual(cleanup('1girl, solo <END> garbage after', 'tags'), '1girl, solo')

    def test_strips_partial_end_tags(self):
        self.assertEqual(cleanup('1girl, solo</END>', 'tags'), '1girl, solo')

    def test_dedupes_repeated_blocks(self):
        raw = '1girl, smile, park\n\n1girl, smile, park\n\n1girl, smile, park'
        self.assertEqual(cleanup(raw, 'tags'), '1girl, smile, park')

    def test_collapses_newlines_to_one_line(self):
        self.assertEqual(cleanup('1girl,\nsmile,\npark', 'tags'), '1girl, smile, park')

    def test_trailing_comma_and_double_commas(self):
        self.assertEqual(cleanup('1girl,, smile, ', 'tags'), '1girl, smile')

    def test_undesired_block_preserved(self):
        raw = '1girl, smile\n\nUNDESIRED: extra limbs, blurry'
        self.assertEqual(cleanup(raw, 'tags'), '1girl, smile\n\nUNDESIRED: extra limbs, blurry')

    def test_code_fences_stripped(self):
        self.assertEqual(cleanup('```text\n1girl, smile\n```', 'tags'), '1girl, smile')

    def test_empty_input(self):
        self.assertEqual(cleanup('', 'tags'), '')
        self.assertEqual(cleanup(None, 'tags'), '')

    def test_think_block_stripped(self):
        raw = '<think>the user wants a knight\n\nso tags...</think>1knight, castle stairs'
        self.assertEqual(cleanup(raw, 'tags'), '1knight, castle stairs')

    def test_unclosed_think_keeps_tail_after_close(self):
        raw = 'reasoning without open tag</think>1girl, smile'
        self.assertEqual(cleanup(raw, 'tags'), '1girl, smile')

    def test_stray_open_think_removed(self):
        self.assertEqual(cleanup('<think>1girl, smile', 'tags'), '1girl, smile')


class SplitUndesired(unittest.TestCase):
    def test_split_and_rejoin(self):
        text = '1girl, smile\n\nUNDESIRED: extra limbs, blurry'
        main, und = split_undesired(text)
        self.assertEqual(main, '1girl, smile')
        self.assertEqual(und, 'extra limbs, blurry')
        self.assertEqual(join_undesired(main, und), '1girl, smile\n\nUNDESIRED: extra limbs, blurry')

    def test_no_undesired(self):
        main, und = split_undesired('1girl, smile')
        self.assertEqual((main, und), ('1girl, smile', ''))
        self.assertEqual(join_undesired(main, und), '1girl, smile')

    def test_empty(self):
        self.assertEqual(split_undesired(''), ('', ''))


class CleanupNatural(unittest.TestCase):
    def test_single_paragraph_whitespace(self):
        raw = 'A 35mm photograph of  a quiet\nstairwell.\n\nA 35mm photograph of  a quiet\nstairwell.'
        self.assertEqual(cleanup(raw, 'natural'), 'A 35mm photograph of a quiet stairwell.')


class Prompts(unittest.TestCase):
    VALUES = {'tag_target': 37, 'word_target': 220}

    def setUp(self):
        import tempfile
        self._orig = prompts.PROMPTS_DIR
        self._tmp = tempfile.TemporaryDirectory()
        prompts.PROMPTS_DIR = Path(self._tmp.name)
        prompts.ensure_files()

    def tearDown(self):
        prompts.PROMPTS_DIR = self._orig
        self._tmp.cleanup()

    def test_nai_soft_target_wording(self):
        p = prompts.build_system('nai', [], self.VALUES)
        self.assertIn('roughly 37 tags', p)
        self.assertIn('go over', p)
        self.assertIn('<END>', p)
        self.assertNotIn('EMPHASIS', p)
        self.assertNotIn('UNDESIRED', p)

    def test_nai_appends_inserted_before_end(self):
        p = prompts.build_system('nai', ['nai_emphasis', 'nai_undesired'], self.VALUES)
        self.assertIn('EMPHASIS', p)
        self.assertIn('UNDESIRED', p)
        # the stop instruction must remain last
        self.assertTrue(p.rstrip().endswith('Write nothing after it.'))

    def test_append_family_filter(self):
        # emphasis is a tags-family append; it must not leak into a natural prompt
        p = prompts.build_system('krea', ['nai_emphasis'], self.VALUES)
        self.assertNotIn('EMPHASIS', p)

    def test_append_order_follows_selection(self):
        ab = prompts.build_system('nai', ['nai_undesired', 'nai_emphasis'], self.VALUES)
        ba = prompts.build_system('nai', ['nai_emphasis', 'nai_undesired'], self.VALUES)
        self.assertLess(ab.index('UNDESIRED'), ab.index('EMPHASIS'))
        self.assertLess(ba.index('EMPHASIS'), ba.index('UNDESIRED'))

    def test_missing_append_id_ignored(self):
        p = prompts.build_system('nai', ['does_not_exist', 'nai_emphasis'], self.VALUES)
        self.assertIn('EMPHASIS', p)

    def test_krea_soft_target_wording(self):
        p = prompts.build_system('krea', [], self.VALUES)
        self.assertIn('roughly 220 words', p)
        self.assertIn('goal, not a hard limit', p)
        self.assertIn('<END>', p)

    def test_refine_message_fallback(self):
        self.assertIn('tighten it', prompts.refine_message('   '))
        self.assertIn('colder light', prompts.refine_message('colder light'))

    def test_variation_message_notes(self):
        self.assertNotIn('Also apply', prompts.variation_message(''))
        self.assertIn('Also apply:\ndrop the coat', prompts.variation_message('drop the coat'))

    def test_enrich_message(self):
        m = prompts.enrich_message('the background')
        self.assertIn('detail about: the background', m)
        self.assertIn('invent specific new details freely', m)
        self.assertIn('thinnest', prompts.enrich_message('  '))


class PromptFiles(unittest.TestCase):
    """Templates are editable files, re-read on every render."""

    def setUp(self):
        import tempfile
        self._orig = prompts.PROMPTS_DIR
        self._tmp = tempfile.TemporaryDirectory()
        prompts.PROMPTS_DIR = Path(self._tmp.name)

    def tearDown(self):
        prompts.PROMPTS_DIR = self._orig
        self._tmp.cleanup()

    def test_ensure_files_creates_all(self):
        prompts.ensure_files()
        for rel in prompts.DEFAULT_FILES:
            self.assertTrue((prompts.PROMPTS_DIR / rel).exists(), rel)
        self.assertTrue((prompts.PROMPTS_DIR / 'catalog.json').exists())

    def test_edited_file_is_used_live(self):
        prompts.ensure_files()
        (prompts.PROMPTS_DIR / 'system/krea.txt').write_text(
            'CUSTOM PROMPT aiming for $word_target words', encoding='utf-8')
        self.assertEqual(prompts.build_system('krea', [], {'word_target': 99}),
                         'CUSTOM PROMPT aiming for 99 words')

    def test_missing_file_falls_back_to_default(self):
        # dir intentionally left empty - no ensure_files
        self.assertIn('roughly 42 words', prompts.build_system('krea', [], {'word_target': 42}))

    def test_custom_prompt_added_to_catalog(self):
        prompts.ensure_files()
        (prompts.PROMPTS_DIR / 'system/noir.txt').write_text(
            'Noir prompt, $word_target words', encoding='utf-8')
        cat = prompts.load_catalog()
        cat['system'].append({'id': 'noir', 'name': 'Noir', 'file': 'system/noir.txt',
                              'family': 'natural'})
        prompts.save_catalog(cat)
        self.assertIn('noir', prompts.system_options())
        self.assertEqual(prompts.family_of('noir'), 'natural')
        self.assertIn('Noir prompt', prompts.build_system('noir', [], {'word_target': 80}))

    def test_create_update_delete_cycle(self):
        prompts.ensure_files()
        pid = prompts.create_entry('system', 'My Noir Look',
                                   'Noir, $word_target words', family='natural')
        self.assertEqual(pid, 'my_noir_look')
        self.assertIn(pid, prompts.system_options())
        self.assertEqual(prompts.family_of(pid), 'natural')
        prompts.update_entry('system', pid, name='Renamed', text='New text, $word_target')
        self.assertEqual(prompts.system_options()[pid], 'Renamed')
        self.assertIn('New text', prompts.build_system(pid, [], {'word_target': 50}))
        self.assertTrue(prompts.delete_entry('system', pid))
        self.assertNotIn(pid, prompts.system_options())

    def test_builtins_are_protected(self):
        prompts.ensure_files()
        self.assertFalse(prompts.delete_entry('system', 'nai'))  # cannot delete
        (prompts.PROMPTS_DIR / 'system/nai.txt').write_text('mangled', encoding='utf-8')
        self.assertTrue(prompts.reset_builtin('system', 'nai'))  # restores default
        self.assertIn('Danbooru', prompts.build_system('nai', [], {'tag_target': 30}))

    def test_slug_collision(self):
        prompts.ensure_files()
        a = prompts.create_entry('appends', 'Warm', 'warm palette')
        b = prompts.create_entry('appends', 'Warm', 'warmer palette')
        self.assertNotEqual(a, b)


class SettingsMigration(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pf import settings
        self.settings = settings
        self._orig = settings.CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        settings.CONFIG_PATH = Path(self._tmp.name) / 'config.json'

    def tearDown(self):
        self.settings.CONFIG_PATH = self._orig
        self._tmp.cleanup()

    def _write(self, data: dict):
        import json
        self.settings.CONFIG_PATH.write_text(json.dumps(data), encoding='utf-8')

    def test_missing_file_gives_defaults(self):
        cfg = self.settings.load()
        self.assertEqual(cfg['model'], 'glm-4-6')
        self.assertEqual(cfg['theme'], 'auto')

    def test_legacy_use_xialong_migrates_to_model(self):
        self._write({'use_xialong': True})
        self.assertEqual(self.settings.load()['model'], 'xialong-v1')
        self._write({'use_xialong': False})
        self.assertEqual(self.settings.load()['model'], 'glm-4-6')

    def test_legacy_dark_migrates_to_theme(self):
        self._write({'dark': True})
        self.assertEqual(self.settings.load()['theme'], 'dark')
        self._write({'dark': False})
        self.assertEqual(self.settings.load()['theme'], 'light')

    def test_legacy_mode_migrates_to_prompt(self):
        self._write({'mode': 'krea'})
        self.assertEqual(self.settings.load()['prompt'], 'krea')
        self._write({'mode': 'nai'})
        self.assertEqual(self.settings.load()['prompt'], 'nai')

    def test_legacy_switches_migrate_to_active_appends(self):
        self._write({'use_emphasis': True, 'suggest_undesired': True})
        self.assertEqual(sorted(self.settings.load()['active_appends']),
                         ['nai_emphasis', 'nai_undesired'])
        self._write({'use_emphasis': False, 'suggest_undesired': False})
        self.assertEqual(self.settings.load()['active_appends'], [])

    def test_save_drops_unknown_keys(self):
        cfg = self.settings.load()
        cfg['endpoint_style'] = 'openai'  # legacy key must not survive a save
        self.settings.save(cfg)
        import json
        saved = json.loads(self.settings.CONFIG_PATH.read_text(encoding='utf-8'))
        self.assertNotIn('endpoint_style', saved)
        self.assertNotIn('use_xialong', saved)


class Models(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pf import models
        self.models = models
        self._orig = models.MODELS_PATH
        self._tmp = tempfile.TemporaryDirectory()
        models.MODELS_PATH = Path(self._tmp.name) / 'models.json'

    def tearDown(self):
        self.models.MODELS_PATH = self._orig
        self._tmp.cleanup()

    def test_ensure_and_load_defaults(self):
        self.models.ensure_file()
        self.assertTrue(self.models.MODELS_PATH.exists())
        loaded = self.models.load()
        self.assertIn('glm-4-6', loaded)
        self.assertIn('xialong-v1', self.models.options())

    def test_description_lookup(self):
        self.assertIn('NovelAI', self.models.description('xialong-v1'))
        self.assertEqual(self.models.description('nonexistent'), '')

    def test_custom_model_file(self):
        import json
        self.models.MODELS_PATH.write_text(
            json.dumps({'my-model': {'name': 'My Model', 'description': 'mine'}}),
            encoding='utf-8')
        self.assertEqual(self.models.options(), {'my-model': 'My Model'})
        self.assertEqual(self.models.description('my-model'), 'mine')

    def test_malformed_falls_back(self):
        self.models.MODELS_PATH.write_text('not json', encoding='utf-8')
        self.assertIn('glm-4-6', self.models.load())


class DebugLog(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pf import debuglog
        self.dl = debuglog
        self._orig_dir, self._orig_file = debuglog.LOG_DIR, debuglog.LOG_FILE
        self._tmp = tempfile.TemporaryDirectory()
        debuglog.LOG_DIR = Path(self._tmp.name)
        debuglog.LOG_FILE = Path(self._tmp.name) / 'prompt_forge.log'
        debuglog.lines.clear()
        debuglog.set_level('off')

    def tearDown(self):
        self.dl.LOG_DIR, self.dl.LOG_FILE = self._orig_dir, self._orig_file
        self.dl.lines.clear()
        self.dl.set_level('off')
        self._tmp.cleanup()

    def test_off_records_nothing(self):
        self.dl.set_level('off')
        self.dl.log('hello', 'basic')
        self.assertEqual(self.dl.lines, [])
        self.assertFalse(self.dl.LOG_FILE.exists())

    def test_basic_records_basic_not_verbose(self):
        self.dl.set_level('basic')
        self.dl.log('step', 'basic')
        self.dl.log('detail', 'verbose')
        self.assertEqual(len(self.dl.lines), 1)
        self.assertIn('step', self.dl.lines[0])
        self.assertTrue(self.dl.LOG_FILE.exists())

    def test_verbose_records_data_and_blob(self):
        self.dl.set_level('verbose')
        self.dl.log('with data', 'verbose', {'a': 1})
        self.assertTrue(any('"a": 1' in ln for ln in self.dl.lines))
        p = self.dl.save_blob('request.json', {'x': 2})
        self.assertIsNotNone(p)
        self.assertIn('"x": 2', p.read_text(encoding='utf-8'))

    def test_blob_skipped_below_verbose(self):
        self.dl.set_level('basic')
        self.assertIsNone(self.dl.save_blob('r.json', {'x': 1}))

    def test_token_default_is_off(self):
        from pf import settings
        self.assertEqual(settings.DEFAULTS['log_level'], 'off')


class Presets(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pf import presets
        self.presets = presets
        self._orig = presets.PRESETS_PATH
        self._tmp = tempfile.TemporaryDirectory()
        presets.PRESETS_PATH = Path(self._tmp.name) / 'presets.json'

    def tearDown(self):
        self.presets.PRESETS_PATH = self._orig
        self._tmp.cleanup()

    def test_defaults_and_options(self):
        self.presets.ensure_file()
        self.assertIn('balanced', self.presets.options())
        self.assertEqual(self.presets.get_params('balanced')['temperature'], 0.75)

    def test_get_params_fills_missing_keys(self):
        # a preset missing a key still returns every param via defaults
        import json
        self.presets.PRESETS_PATH.write_text(
            json.dumps({'x': {'name': 'X', 'builtin': False,
                              'params': {'temperature': 1.2}}}), encoding='utf-8')
        p = self.presets.get_params('x')
        self.assertEqual(p['temperature'], 1.2)
        self.assertEqual(p['top_p'], 0.95)  # default filled
        self.assertEqual(set(p), set(self.presets.PARAM_KEYS))

    def test_create_update_delete(self):
        self.presets.ensure_file()
        pid = self.presets.create('Wild', {'temperature': 1.4, 'top_k': 40})
        self.assertEqual(self.presets.get_params(pid)['temperature'], 1.4)
        self.assertEqual(self.presets.get_params(pid)['top_k'], 40)
        self.presets.update(pid, params={'temperature': 0.9})
        self.assertEqual(self.presets.get_params(pid)['temperature'], 0.9)
        self.assertTrue(self.presets.delete(pid))
        self.assertNotIn(pid, self.presets.options())

    def test_builtins_protected(self):
        self.presets.ensure_file()
        self.assertFalse(self.presets.delete('balanced'))
        self.presets.update('balanced', params={'temperature': 9.9})  # ignored
        self.assertEqual(self.presets.get_params('balanced')['temperature'], 0.75)


class PayloadKnobs(unittest.TestCase):
    def _payload(self, **params):
        from pf.nai_client import NAIClient, make_params
        gp = make_params('glm-4-6', 900, {**{'temperature': 0.7}, **params})
        return NAIClient('tok')._payload([{'role': 'user', 'content': 'hi'}], gp, stream=True)

    def test_off_values_omitted(self):
        pl = self._payload(top_k=0, min_p=0.0, seed=-1, unified_linear=0.0)
        for k in ('top_k', 'min_p', 'seed', 'unified_linear'):
            self.assertNotIn(k, pl, k)

    def test_set_values_included(self):
        pl = self._payload(top_k=40, min_p=0.05, seed=123, unified_linear=0.3)
        self.assertEqual(pl['top_k'], 40)
        self.assertEqual(pl['min_p'], 0.05)
        self.assertEqual(pl['seed'], 123)
        self.assertEqual(pl['unified_linear'], 0.3)

    def test_always_present(self):
        pl = self._payload()
        for k in ('temperature', 'top_p', 'frequency_penalty', 'presence_penalty',
                  'stream', 'model', 'messages'):
            self.assertIn(k, pl)


class History(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pf import history
        self.history = history
        self._orig = history.HISTORY_PATH
        self._tmp = tempfile.TemporaryDirectory()
        history.HISTORY_PATH = Path(self._tmp.name) / 'history.jsonl'

    def tearDown(self):
        self.history.HISTORY_PATH = self._orig
        self._tmp.cleanup()

    def test_append_load_delete_roundtrip(self):
        self.history.append({'mode': 'nai', 'idea': 'a', 'output': '1girl'})
        self.history.append({'mode': 'krea', 'idea': 'b', 'output': 'a photo'})
        entries = self.history.load()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['idea'], 'b')  # newest first
        self.assertTrue(all(e.get('ts') and e.get('id') for e in entries))
        self.history.delete(entries[0]['id'])
        remaining = self.history.load()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['idea'], 'a')

    def test_corrupt_line_is_skipped(self):
        self.history.append({'idea': 'good', 'output': 'x'})
        with self.history.HISTORY_PATH.open('a', encoding='utf-8') as f:
            f.write('{corrupt json\n')
        self.assertEqual(len(self.history.load()), 1)


if __name__ == '__main__':
    unittest.main()
