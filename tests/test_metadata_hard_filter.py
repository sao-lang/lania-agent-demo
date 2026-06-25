"""验证检索阶段的硬过滤条件归一化与下推行为。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.rag.llamaindex_components import build_metadata_filters
from app.rag.observability import TraceRecorder
from app.rag.retrieval import RagRetrievalService
from app.services.state import InMemoryState


class FakeVectorStoreFactory:
    """提供最小向量库工厂桩对象，避免测试依赖真实存储。"""

    def get_or_create_collection(self, name: str):
        raise NotImplementedError


class MetadataHardFilterTests(unittest.TestCase):
    """覆盖标签、年份、季度、版本和权限等元数据过滤规则。"""

    def setUp(self) -> None:
        """构造一个只关注过滤逻辑的检索服务实例。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        with patch('app.rag.retrieval.build_embed_model', return_value=object()):
            self.service = RagRetrievalService(
                self.settings,
                self.state,
                FakeVectorStoreFactory(),
                self.trace,
            )

    def test_tags_subset_filter(self) -> None:
        """验证默认标签过滤要求查询标签是文档标签集合的子集。"""
        metadata = {'tags': 'guide|api|rag'}
        self.assertTrue(self.service._matches_filters(metadata, {'tags': ['guide']}))
        self.assertTrue(self.service._matches_filters(metadata, {'tags': ['guide', 'api']}))
        self.assertFalse(self.service._matches_filters(metadata, {'tags': ['missing']}))

    def test_tags_any_mode_filter(self) -> None:
        """验证 `any` 模式只要任一标签命中即可通过过滤。"""
        metadata = {'tags': 'guide|api|rag'}
        self.assertTrue(self.service._matches_filters(metadata, {'tags': {'mode': 'any', 'values': ['missing', 'api']}}))
        self.assertFalse(self.service._matches_filters(metadata, {'tags': {'mode': 'any', 'values': ['missing']}}))

    def test_year_exact_and_in_filter(self) -> None:
        """验证年份过滤同时支持精确匹配和离散值集合匹配。"""
        metadata = {'year': '2026'}
        self.assertTrue(self.service._matches_filters(metadata, {'year': 2026}))
        self.assertTrue(self.service._matches_filters(metadata, {'year': ['2025', 2026]}))
        self.assertFalse(self.service._matches_filters(metadata, {'year': 2025}))

    def test_year_range_filter(self) -> None:
        """验证年份区间过滤会按上下界比较归一化后的整数值。"""
        metadata = {'year': '2026'}
        self.assertTrue(self.service._matches_filters(metadata, {'year': {'gte': 2025, 'lte': 2026}}))
        self.assertFalse(self.service._matches_filters(metadata, {'year': {'gte': 2027}}))

    def test_quarter_normalization(self) -> None:
        """验证季度值会被统一成数字后再进行比较。"""
        metadata = {'quarter': 'Q2'}
        self.assertTrue(self.service._matches_filters(metadata, {'quarter': 2}))
        self.assertTrue(self.service._matches_filters(metadata, {'quarter': ['Q1', '2']}))
        self.assertFalse(self.service._matches_filters(metadata, {'quarter': 'Q3'}))

    def test_version_prefix_filter(self) -> None:
        """验证版本号前缀过滤适用于语义版本的前缀匹配场景。"""
        metadata = {'version': 'v1.2.3'}
        self.assertTrue(self.service._matches_filters(metadata, {'version': {'prefix': 'v1.2'}}))
        self.assertFalse(self.service._matches_filters(metadata, {'version': {'prefix': 'v2'}}))

    def test_permission_filter_supports_alias_and_in(self) -> None:
        """验证权限过滤兼容中文别名以及 `in` 集合写法。"""
        metadata = {'permission': 'internal'}
        self.assertTrue(self.service._matches_filters(metadata, {'permission': '内部'}))
        self.assertTrue(self.service._matches_filters(metadata, {'permission': {'in': ['public', 'internal']}}))
        self.assertFalse(self.service._matches_filters(metadata, {'permission': 'confidential'}))

    def test_build_metadata_filters_pushes_down_supported_hard_filters(self) -> None:
        """验证可下推的硬过滤条件会被转换为底层向量库过滤表达式。"""
        filters = build_metadata_filters(
            {
                'year': {'gte': 2025, 'lte': 2026},
                'quarter': [1, 'Q2'],
                'version': {'eq': 'v1.2.3'},
                'permission': {'in': ['内部', 'restricted']},
                'tags': ['guide'],
            }
        )

        self.assertIsNotNone(filters)
        payload = filters.model_dump(mode='json')
        entries = payload['filters']
        self.assertIn(
            {
                'filters': [
                    {'key': 'year_int', 'value': 2025, 'operator': '>='},
                    {'key': 'year_int', 'value': 2026, 'operator': '<='},
                ],
                'condition': 'and',
            },
            entries,
        )
        self.assertIn({'key': 'quarter_num', 'value': [1, 2], 'operator': 'in'}, entries)
        self.assertIn({'key': 'version', 'value': 'v1.2.3', 'operator': '=='}, entries)
        self.assertIn({'key': 'permission', 'value': ['internal', 'restricted'], 'operator': 'in'}, entries)


if __name__ == '__main__':
    unittest.main()
