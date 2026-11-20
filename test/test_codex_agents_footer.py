import unittest
from provider_execution.draft_observation import inspect_screen

class FooterCompatibilityTests(unittest.TestCase):
    def observe(self, hint='← for agents · ? for shortcuts', content='Ask Codex to do anything', body='  Worked for 23s • 5:33 PM'):
        rows = [body, '', '› ' + content, '', '  custom-model low · workspace · title', '  ' + hint]
        return inspect_screen('codex', {'text': '\n'.join(rows), 'cursor_x': 2, 'cursor_y': 2}, binding='test')

    def test_current_native_empty_composer(self):
        self.assertEqual(self.observe().state, 'empty')

    def test_current_native_hint_with_warning(self):
        self.assertEqual(self.observe('← for agents · ? for shortcuts   ⚠ 4 warnings · f2 to view').state, 'empty')

    def test_legacy_hint_still_works(self):
        self.assertEqual(self.observe('? for shortcuts').state, 'empty')

    def test_busy_stays_blocked(self):
        self.assertEqual(self.observe(body='• Working (5s • esc to interrupt)').reason, 'provider_busy')

    def test_human_draft_is_not_empty(self):
        self.assertNotEqual(self.observe(content='保留用户未发送输入').state, 'empty')

    def test_arbitrary_footer_stays_unknown(self):
        self.assertEqual(self.observe('arbitrary draft continuation').state, 'unknown')

    def test_menu_hint_is_not_native_footer(self):
        self.assertEqual(self.observe('← choose an option · ? for shortcuts').state, 'unknown')

    def test_editor_mode_stays_unknown(self):
        self.assertEqual(self.observe('← for agents · ? for shortcuts NORMAL').state, 'unknown')

if __name__ == '__main__':
    unittest.main()
