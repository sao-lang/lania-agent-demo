"""Context Harness v2 单元测试。"""

import unittest

from app.harness.budgeting import TokenBudgetEngine
from app.harness.compression import CompressionEngine, DeduplicationCompression, HierarchicalCompression
from app.harness.context_policy import ContextPolicy, ContextSourceType
from app.harness.selection import CombinedSelection, RelevanceBasedSelection, SelectionEngine


class ContextPolicyTests(unittest.TestCase):
    """ContextPolicy 模型测试。"""
    
    def test_for_step_returns_correct_policy(self) -> None:
        """测试 for_step 方法返回正确的策略。"""
        policy = ContextPolicy.for_step('analyze')
        self.assertEqual(policy.step_type, 'analyze')
        self.assertEqual(policy.evidence_top_k, 8)
        self.assertTrue(policy.compression_enabled)
    
    def test_for_step_returns_default_for_unknown(self) -> None:
        """测试未知 step 返回默认策略。"""
        policy = ContextPolicy.for_step('unknown_step')
        self.assertEqual(policy.step_type, 'unknown_step')
        self.assertEqual(policy.evidence_top_k, 6)
    
    def test_get_selection_rule(self) -> None:
        """测试获取选择规则。"""
        policy = ContextPolicy.for_step('draft_artifact')
        rule = policy.get_selection_rule(ContextSourceType.EVIDENCE)
        self.assertEqual(rule.source_type, ContextSourceType.EVIDENCE)
        self.assertEqual(rule.top_k, 6)


class RelevanceBasedSelectionTests(unittest.TestCase):
    """相关性选择策略测试。"""
    
    def test_calculate_relevance(self) -> None:
        """测试相关性计算。"""
        selector = RelevanceBasedSelection(intent='分析财务风险')
        
        item = {'summary': '财务风险评估报告显示存在潜在风险'}
        score = selector._calculate_relevance(item, '分析财务风险')
        self.assertGreater(score, 0.3)
        
        item_unrelated = {'summary': '天气晴朗适合出游'}
        score_unrelated = selector._calculate_relevance(item_unrelated, '分析财务风险')
        self.assertLess(score_unrelated, 0.3)
    
    def test_select_filters_by_threshold(self) -> None:
        """测试按阈值过滤。"""
        selector = RelevanceBasedSelection(intent='test content')
        policy = ContextPolicy(step_type='test', evidence_relevance_threshold=0.8)
        
        items = [
            {'summary': 'highly relevant content about test content matching'},
            {'summary': 'somewhat relevant test item'},
            {'summary': 'unrelated content'},
        ]
        
        selected = selector.select(items, policy, ContextSourceType.EVIDENCE)
        self.assertEqual(len(selected), 1)


class CombinedSelectionTests(unittest.TestCase):
    """组合选择策略测试。"""
    
    def test_select_combines_relevance_and_recency(self) -> None:
        """测试组合相关性和时间因素。"""
        selector = CombinedSelection(relevance_weight=0.6, recency_weight=0.4)
        policy = ContextPolicy(step_type='test', evidence_top_k=2)
        
        items = [
            {'summary': 'very relevant', 'created_at': '2024-01-01'},
            {'summary': 'moderately relevant', 'created_at': '2024-01-02'},
            {'summary': 'less relevant', 'created_at': '2024-01-03'},
        ]
        
        selected = selector.select(items, policy, ContextSourceType.EVIDENCE, intent='relevant')
        self.assertEqual(len(selected), 2)


class CompressionTests(unittest.TestCase):
    """压缩策略测试。"""
    
    def test_sentence_truncation(self) -> None:
        """测试句子截断。"""
        policy = ContextPolicy(step_type='test', compression_enabled=True, compression_max_sentences=2, compression_max_chars=200)
        strategy = HierarchicalCompression()
        
        items = [
            {'text': '这是第一句话。这是第二句话。这是第三句话。这是第四句话。'}
        ]
        
        compressed = strategy.compress(items, policy)
        self.assertIn('第一句话', compressed[0]['text'])
        self.assertIn('第二句话', compressed[0]['text'])
        self.assertNotIn('第三句话', compressed[0]['text'])
    
    def test_deduplication_only(self) -> None:
        """测试去重（单独测试）。"""
        items = [
            {'text': '相同的内容'},
            {'text': '相同的内容'},
            {'text': '不同的内容'},
        ]
        
        policy = ContextPolicy(step_type='test')
        strategy = DeduplicationCompression()
        compressed = strategy.compress(items, policy)
        
        self.assertEqual(len(compressed), 2)
    
    def test_compression_ratio(self) -> None:
        """测试压缩率计算。"""
        engine = CompressionEngine()
        original = [{'text': 'a' * 100}]
        compressed = [{'text': 'a' * 50}]
        
        ratio = engine.calculate_compression_ratio(original, compressed)
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 1.0)


class TokenBudgetEngineTests(unittest.TestCase):
    """Token 预算引擎测试。"""
    
    def test_allocate_budget(self) -> None:
        """测试预算分配。"""
        engine = TokenBudgetEngine()
        policy = ContextPolicy(step_type='test', token_budget=10000)
        
        engine.allocate_budget(policy)
        
        total_allocated = sum(alloc.allocated for alloc in engine.allocations.values())
        self.assertEqual(total_allocated, 10000)
    
    def test_estimate_tokens(self) -> None:
        """测试 token 估算。"""
        engine = TokenBudgetEngine()
        
        text = 'hello world'
        tokens = engine.estimate_tokens(text)
        self.assertEqual(tokens, 2)
        
        data = {'key': 'value', 'list': [1, 2, 3]}
        tokens = engine.estimate_tokens(data)
        self.assertGreater(tokens, 0)
    
    def test_enforce_budget(self) -> None:
        """测试预算强制执行。"""
        engine = TokenBudgetEngine()
        policy = ContextPolicy(step_type='test', token_budget=500)
        
        engine.allocate_budget(policy)
        
        context_data = {
            'state_slice': {'data': 'x' * 500},
            'evidence_slice': [{'text': 'x' * 500}],
            'memory_slice': {'task_memory': [{'summary': 'x' * 500}]},
            'artifact_slice': None,
        }
        
        engine.record_usage(ContextSourceType.STATE, context_data['state_slice'])
        engine.record_usage(ContextSourceType.EVIDENCE, context_data['evidence_slice'])
        engine.record_usage(ContextSourceType.MEMORY, context_data['memory_slice'])
        
        enforced = engine.enforce_budget(context_data, policy)
        
        engine2 = TokenBudgetEngine()
        engine2.allocate_budget(policy)
        total_tokens = engine2.estimate_tokens(enforced)
        self.assertLessEqual(total_tokens, 500)


class SelectionEngineTests(unittest.TestCase):
    """选择引擎测试。"""
    
    def test_select_state(self) -> None:
        """测试状态选择。"""
        engine = SelectionEngine()
        policy = ContextPolicy.for_step('analyze')
        
        workflow_state = {
            'task': {'task_id': 'test-123', 'collection_name': 'demo', 'doc_ids': ['doc1', 'doc2']},
            'focus_aspects': ['risk', 'cost', 'schedule', 'quality', 'scope', 'resources'],
            'document_context': {'documents': list(range(20))},
        }
        
        state_slice = engine.select_state(workflow_state, policy)
        
        self.assertEqual(state_slice['task_id'], 'test-123')
        self.assertEqual(len(state_slice['focus_aspects']), 6)
        self.assertEqual(len(state_slice['document_context_documents']), 6)
    
    def test_select_artifact_scope_none(self) -> None:
        """测试 artifact_scope 为 none 时不返回产物。"""
        engine = SelectionEngine()
        policy = ContextPolicy(step_type='test', artifact_scope='none')
        
        draft_content = {'summary': 'test summary', 'report_markdown': 'test content'}
        
        result = engine.select_artifact(draft_content, policy)
        self.assertIsNone(result)
    
    def test_select_artifact_scope_summary(self) -> None:
        """测试 artifact_scope 为 summary 时只返回摘要。"""
        engine = SelectionEngine()
        policy = ContextPolicy(step_type='test', artifact_scope='summary')
        
        draft_content = {'summary': 'test summary', 'report_markdown': 'test content'}
        
        result = engine.select_artifact(draft_content, policy)
        
        self.assertIsNotNone(result)
        self.assertIn('summary', result)
        self.assertNotIn('report_markdown', result)


if __name__ == '__main__':
    unittest.main()