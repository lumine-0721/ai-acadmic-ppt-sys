"""Synthetic boundary tests, distinct from the real-module CLI integration suite."""
import copy
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from academic_ppt_orchestrator.adapters.seaa import _supports
from academic_ppt_orchestrator.contracts.ir import build_ir, merge_slide_status
from academic_ppt_orchestrator.workflow.graph import node_terminal


class IRMergeTests(unittest.TestCase):
    def setUp(self):
        work = Path(os.environ.get('ORCHESTRATOR_TEST_WORK', Path(__file__).resolve().parents[1] / '.tmp-tests'))
        work.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='academic-ppt-ir-v2-', dir=work)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.handoff = {
            'project_id': 'synthetic-boundary', 'revision': 1, 'slide_order': ['S001'],
            'records': {
                'C001': {'summary': 'Synthetic test only', 'kind': 'finding', 'status': 'VERIFIED'},
                'C002': {'summary': 'Another test claim', 'kind': 'question', 'status': 'TO_RETRIEVE'},
                'S001': {'summary': 'Synthetic slide', 'status': 'READY', 'depends_on': ['C001']},
            },
        }
        self.path = self.root / 'PPT_HANDOFF.json'
        self.write_handoff()
        self.manifest = {'project_id': 'synthetic-boundary', 'base_revision': 1,
                         'slides': [{'slide_id': 'S001', 'status': 'READY', 'gaps': []}],
                         'evidence': [self.evidence('E001', 'supports')]}
        self.state = {
            'run_id': 'test', 'run_dir': str(self.root), 'handoff_path': str(self.path),
            'apc_result': {'acceptance': 'accepted', 'payload': {'handoff_path': str(self.path)}},
            'aprd_result': {'acceptance': 'accepted'},
            'seaa_result': {'acceptance': 'accepted',
                            'payload': {'manifest': self.manifest, 'evidence_package': str(self.root)}},
            'evidence_package': str(self.root), 'evidence_decision': {'gate': 'sufficient', 'reason': 'test'},
        }
        self.refresh()

    def evidence(self, eid, relation, status='VERIFIED'):
        return {'evidence_id': eid, 'source_id': 'SRC001', 'status': status,
                'verification_scope': 'Synthetic input; no scientific assertion', 'locator': 'test fixture row 1',
                'claim_links': [{'claim_id': 'C001', 'relation': relation}]}

    def write_handoff(self):
        self.path.write_text(json.dumps(self.handoff), encoding='utf-8')

    def refresh(self):
        self.state['seaa_result']['validation_report'] = {'supports': _supports(self.manifest)}
        (self.root / '02_EVIDENCE_MANIFEST.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        (self.root / '01_SOURCE_MANIFEST.json').write_text(json.dumps({'sources': [{'source_id': 'SRC001'}]}), encoding='utf-8')

    def ir(self):
        self.refresh()
        return build_ir(self.state)

    def test_all_nine_readiness_combinations(self):
        statuses = ['READY', 'PROVISIONAL', 'BLOCKED']
        for a, s in itertools.product(statuses, repeat=2):
            with self.subTest(apc=a, seaa=s):
                result = merge_slide_status({'status': a}, [{'status': s}], True, True)
                self.assertEqual(result['status'], max((a, s), key=statuses.index))
                self.assertEqual(result['status_sources'], {'apc': a, 'seaa': s})

    def test_missing_assessment_is_not_ready(self):
        self.manifest['slides'] = []
        ir = self.ir()
        self.assertEqual(ir['slide_specifications'][0]['status'], 'PROVISIONAL')
        self.assertIn('seaa_slide_assessment_missing', ir['slide_specifications'][0]['status_reasons'])
        self.assertEqual(ir['validation_state']['status'], 'partial')

    def test_retired_assessment_is_missing(self):
        self.manifest['slides'][0]['retired'] = True
        self.assertEqual(self.ir()['slide_specifications'][0]['status'], 'PROVISIONAL')

    def test_unknown_status_is_blocked_and_preserved(self):
        for side in ('apc', 'seaa'):
            with self.subTest(side=side):
                row = {'status': 'UNKNOWN' if side == 'apc' else 'READY'}
                evidence = [{'status': 'UNKNOWN' if side == 'seaa' else 'READY'}]
                result = merge_slide_status(row, evidence, True, True)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['status_sources'][side], 'UNKNOWN')
                self.assertIn(side + '_status_unknown', result['status_reasons'])

    def test_duplicate_assessments_are_blocked(self):
        self.manifest['slides'] *= 2
        self.assertEqual(self.ir()['slide_specifications'][0]['status'], 'BLOCKED')

    def test_failed_validation_blocks_readiness(self):
        for module in ('apc', 'seaa'):
            with self.subTest(module=module):
                self.state[module + '_result']['acceptance'] = 'blocked'
                ir = self.ir()
                self.assertEqual(ir['slide_specifications'][0]['status'], 'BLOCKED')
                self.assertEqual(ir['validation_state']['status'], 'blocked')
                self.state[module + '_result']['acceptance'] = 'accepted'

    def test_failed_design_validation_blocks_deck(self):
        self.state['aprd_result']['acceptance'] = 'blocked'
        self.assertEqual(self.ir()['validation_state']['status'], 'blocked')

    def test_cross_project_conflict_blocks_pages(self):
        self.state['evidence_decision']['gate'] = 'conflict'
        ir = self.ir()
        self.assertEqual(ir['slide_specifications'][0]['status'], 'BLOCKED')

    def test_missing_package_blocks_deck_without_claiming_failed_assessment(self):
        self.state['seaa_result'] = {}
        self.state['evidence_decision']['gate'] = 'missing'
        ir = self.ir()
        self.assertEqual(ir['slide_specifications'][0]['status'], 'PROVISIONAL')
        self.assertEqual(ir['validation_state']['status'], 'blocked')

    def test_gaps_are_preserved_and_prevent_ready(self):
        self.manifest['slides'][0]['gaps'] = ['Original source has not been read']
        spec = self.ir()['slide_specifications'][0]
        self.assertEqual(spec['evidence_gaps'], self.manifest['slides'][0]['gaps'])
        self.assertEqual(spec['status'], 'PROVISIONAL')

    def test_deck_cannot_accept_provisional_or_blocked_page(self):
        for status, expected in [('PROVISIONAL', 'partial'), ('BLOCKED', 'blocked')]:
            with self.subTest(status=status):
                self.handoff['records']['S001']['status'] = status
                self.write_handoff()
                ir = self.ir()
                self.assertEqual(ir['validation_state']['status'], expected)

    def test_merge_does_not_change_inputs_and_records_adjustment(self):
        self.manifest['slides'][0]['status'] = 'PROVISIONAL'
        self.refresh()
        snapshot = copy.deepcopy(self.state)
        before = self.path.read_bytes()
        ir = build_ir(self.state)
        self.assertEqual(self.state, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(ir['validation_state']['adjustments'][0]['kind'], 'merge_slide_readiness')

    def test_terminal_reason_includes_page_blocker(self):
        self.handoff['records']['S001']['status'] = 'BLOCKED'
        self.write_handoff()
        result = node_terminal({**self.state, 'ir': self.ir(), 'task': {}})
        self.assertEqual(result['terminal'], 'BLOCKED')
        self.assertIn('slide_S001_blocked', result['terminal_reason'])


if __name__ == '__main__':
    unittest.main()
