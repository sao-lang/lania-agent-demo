"""导入增强测试，覆盖语义分块、多格式解析、OCR 回退、压缩包展开和转换缓存治理。"""

import os
import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile
import subprocess

from app.core.config import Settings
from app.rag.ingestion import RagIngestionService
from app.rag.observability import TraceRecorder
from app.services.state import InMemoryState


class FakeVectorStoreFactory:
    """测试桩 `FakeVectorStoreFactory`，用于以最小实现模拟外部依赖或复杂组件，便于稳定断言目标行为。"""
    def delete_chunks(self, collection_name: str, chunk_ids: list[str]) -> None:
        return None


class DummyPipeline:
    """轻量假实现 `DummyPipeline`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, transformations, vector_store) -> None:
        self.transformations = transformations
        self.vector_store = vector_store


class DummySemanticParser:
    """轻量假实现 `DummySemanticParser`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class DummySentenceSplitter:
    """轻量假实现 `DummySentenceSplitter`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class DummyWorksheet:
    """轻量假实现 `DummyWorksheet`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, title: str, rows: list[tuple[object, ...]]) -> None:
        self.title = title
        self._rows = rows

    def iter_rows(self, values_only: bool = False):
        return iter(self._rows)


class DummyWorkbook:
    """轻量假实现 `DummyWorkbook`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, worksheets: list[DummyWorksheet]) -> None:
        self.worksheets = worksheets


class DummyShape:
    """轻量假实现 `DummyShape`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, text: str, *, is_title: bool = False, name: str = '') -> None:
        self.text = text
        self.is_title = is_title
        self.name = name


class DummySlide:
    """轻量假实现 `DummySlide`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, shapes: list[DummyShape], notes_text: str | None = None) -> None:
        self.shapes = shapes
        self.notes_text = notes_text


class DummyPresentation:
    """轻量假实现 `DummyPresentation`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, slides: list[DummySlide]) -> None:
        self.slides = slides


class DummyWhisperModel:
    """轻量假实现 `DummyWhisperModel`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def transcribe(self, path: str, fp16: bool = False) -> dict[str, object]:
        return self.result


class DummyImage:
    """轻量假实现 `DummyImage`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, width: int = 640, height: int = 480) -> None:
        self.width = width
        self.height = height

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def convert(self, mode: str):
        return self

    def point(self, func):
        return self


class DummyPdfPage:
    """轻量假实现 `DummyPdfPage`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class DummyPdfReader:
    """轻量假实现 `DummyPdfReader`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, pages: list[DummyPdfPage]) -> None:
        self.pages = pages


class DummyPdfPlumberPage:
    """轻量假实现 `DummyPdfPlumberPage`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(
        self,
        words: list[dict[str, object]],
        width: float = 600,
        height: float = 800,
        images: list[dict[str, object]] | None = None,
    ) -> None:
        self._words = words
        self.width = width
        self.height = height
        self.images = images or []

    def extract_words(self):
        return self._words


class DummyPdfPlumberDocument:
    """轻量假实现 `DummyPdfPlumberDocument`，只保留当前测试所需的最小字段与方法，减少样板依赖。"""
    def __init__(self, pages: list[DummyPdfPlumberPage]) -> None:
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class IngestionEnhancementTests(unittest.TestCase):
    """导入增强测试集合，确保不同文档解析增强能力按预期工作。"""
    def setUp(self) -> None:
        """初始化当前测试所需的隔离环境、依赖桩和样例数据，避免不同用例之间互相污染。"""
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.settings = Settings(DATA_DIR=self.tmp_dir)
        self.state = InMemoryState()
        self.trace = TraceRecorder()
        self.vector_store = FakeVectorStoreFactory()

    def _build_service(self, settings: Settings | None = None) -> RagIngestionService:
        """封装当前测试反复使用的构造步骤，减少样板代码并突出断言重点。"""
        with patch('app.rag.ingestion.build_embed_model', return_value=object()):
            return RagIngestionService(
                settings or self.settings,
                self.state,
                self.vector_store,
                self.trace,
            )

    def test_prepare_segments_cleans_noise_and_enriches_metadata(self) -> None:
        """覆盖 `prepare_segments_cleans_noise_and_enriches_metadata` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        file_path = self.tmp_dir / 'internal' / '产品手册_2026Q1_v1.2.md'
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            '\n'.join(
                [
                    '# 总览',
                    'Page 1 of 3',
                    '这是 2026 年 Q1 产品升级说明，介绍整体能力。',
                    '## 安装步骤',
                    '仅内部使用',
                    'Page 2 of 3',
                    '安装步骤如下：先执行初始化，再执行部署。',
                ]
            ),
            encoding='utf-8',
        )
        record = {
            'doc_id': 'doc-1',
            'file_name': file_path.name,
            'file_path': str(file_path),
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': ['guide'],
        }

        raw_segments = service._extract_segments(file_path)
        segments, metadata = service._prepare_segments(record, raw_segments)
        documents = service._build_documents({**record, **metadata}, segments)

        self.assertEqual(metadata['year'], '2026')
        self.assertEqual(metadata['quarter'], 'Q1')
        self.assertEqual(metadata['version'], 'v1.2')
        self.assertEqual(metadata['permission'], 'internal')
        self.assertIn('demo', metadata['document_hierarchy'])
        self.assertTrue(metadata['document_keywords'])
        self.assertEqual(segments[0]['section_level'], 1)
        self.assertEqual(segments[1]['hierarchy_path'], '总览 > 安装步骤')
        self.assertNotIn('Page 1 of 3', segments[0]['text'])
        self.assertIn('segment_summary', segments[0])
        self.assertIn('segment_keywords', segments[0])
        self.assertEqual(documents[0].metadata['year'], '2026')
        self.assertEqual(documents[0].metadata['year_int'], 2026)
        self.assertEqual(documents[0].metadata['quarter_num'], 1)
        self.assertEqual(documents[0].metadata['permission'], 'internal')
        self.assertTrue(documents[0].metadata['parent_chunk_id'].startswith('doc-1-parent-'))
        self.assertIn('章节：总览', documents[0].metadata['parent_context'])
        hint_docs = [item for item in documents if item.metadata.get('index_kind') == 'query_hint']
        self.assertTrue(hint_docs)
        self.assertEqual(hint_docs[0].metadata['retrieval_target_chunk_id'], 'doc-1-segment-0001')
        self.assertEqual(hint_docs[0].metadata['retrieval_target_text'], documents[0].text)
        self.assertIn('document_keywords', documents[0].metadata)
        self.assertIn('chapter_tags', documents[0].metadata)
        self.assertIsInstance(documents[0].metadata['document_keywords'], str)
        self.assertIsInstance(documents[0].metadata['segment_keywords'], str)
        self.assertIsInstance(documents[0].metadata['chapter_tags'], str)
        self.assertIsInstance(hint_docs[0].metadata['document_keywords'], str)
        self.assertIsInstance(hint_docs[0].metadata['segment_keywords'], str)
        self.assertIsInstance(hint_docs[0].metadata['chapter_tags'], str)

    def test_build_pipeline_uses_semantic_chunking_when_enabled(self) -> None:
        """覆盖 `build_pipeline_uses_semantic_chunking_when_enabled` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        settings = Settings(
            DATA_DIR=self.tmp_dir,
            INGESTION_CHUNKING_STRATEGY='semantic',
            SEMANTIC_CHUNK_BUFFER_SIZE=2,
            SEMANTIC_CHUNK_BREAKPOINT_PERCENTILE=90,
        )
        self.state.collections['demo'] = {'chunking_strategy': 'semantic'}
        service = self._build_service(settings)

        with (
            patch('app.rag.ingestion.build_vector_store', return_value=object()),
            patch('app.rag.ingestion.IngestionPipeline', DummyPipeline),
            patch(
                'app.rag.ingestion.importlib.import_module',
                return_value=SimpleNamespace(SemanticSplitterNodeParser=DummySemanticParser),
            ),
        ):
            pipeline = service._build_pipeline('demo')

        self.assertIsInstance(pipeline.transformations[0], DummySemanticParser)
        self.assertEqual(pipeline.transformations[0].kwargs['buffer_size'], 2)
        self.assertEqual(pipeline.transformations[0].kwargs['breakpoint_percentile_threshold'], 90)

    def test_build_pipeline_falls_back_to_sentence_splitter_when_semantic_parser_missing(self) -> None:
        """覆盖 `build_pipeline_falls_back_to_sentence_splitter_when_semantic_parser_missing` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        settings = Settings(DATA_DIR=self.tmp_dir, INGESTION_CHUNKING_STRATEGY='semantic')
        self.state.collections['demo'] = {'chunking_strategy': 'semantic'}
        service = self._build_service(settings)

        with (
            patch('app.rag.ingestion.build_vector_store', return_value=object()),
            patch('app.rag.ingestion.IngestionPipeline', DummyPipeline),
            patch('app.rag.ingestion.SentenceSplitter', DummySentenceSplitter),
            patch('app.rag.ingestion.importlib.import_module', side_effect=ModuleNotFoundError('missing semantic parser')),
        ):
            pipeline = service._build_pipeline('demo')

        self.assertIsInstance(pipeline.transformations[0], DummySentenceSplitter)
        self.assertEqual(self.trace.events[-1].name, 'semantic_chunking_fallback')

    def test_prepare_segments_merges_body_segments_for_semantic_chunking(self) -> None:
        """覆盖 `prepare_segments_merges_body_segments_for_semantic_chunking` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        settings = Settings(DATA_DIR=self.tmp_dir, INGESTION_CHUNKING_STRATEGY='semantic', DEFAULT_CHUNK_SIZE=300)
        self.state.collections['demo'] = {'chunking_strategy': 'semantic'}
        service = self._build_service(settings)
        segments = [
            {
                'text': '第一段正文 ' * 20,
                'section_title': '总览',
                'hierarchy_path': '总览',
                'segment_summary': '第一段',
                'segment_keywords': ['正文'],
                'chapter_tags': ['总览'],
            },
            {
                'text': '第二段正文 ' * 18,
                'section_title': '总览',
                'hierarchy_path': '总览',
                'segment_summary': '第二段',
                'segment_keywords': ['正文'],
                'chapter_tags': ['总览'],
            },
            {
                'text': '图 1：架构说明',
                'section_title': '总览',
                'hierarchy_path': '总览',
                'pdf_block_role': 'figure_caption',
            },
        ]

        prepared = service._prepare_segments_for_chunking(
            {'collection_name': 'demo'},
            segments,
        )

        self.assertEqual(len(prepared), 2)
        self.assertEqual(prepared[0]['chunking_strategy_effective'], 'semantic')
        self.assertEqual(prepared[0]['source_segment_count'], 2)
        self.assertTrue(prepared[0]['chunking_prepared'])
        self.assertIn('第一段正文', prepared[0]['text'])
        self.assertIn('第二段正文', prepared[0]['text'])
        self.assertEqual(prepared[1]['chunking_strategy_effective'], 'fixed')
        self.assertEqual(prepared[1]['pdf_block_role'], 'figure_caption')

    def test_extract_segments_supports_code_and_zip_inputs(self) -> None:
        """覆盖 `extract_segments_supports_code_and_zip_inputs` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        code_path = self.tmp_dir / 'example.py'
        code_path.write_text('def hello():\n    return "world"\n', encoding='utf-8')

        code_segments = service._extract_segments(code_path)
        self.assertEqual(code_segments[0]['code_language'], 'py')
        self.assertIn('def hello', code_segments[0]['text'])

        archive_path = self.tmp_dir / 'bundle.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('docs/readme.md', '# Intro\nzip content')
            archive.writestr('src/app.py', 'print("zip code")\n')

        archive_segments = service._extract_segments(archive_path)
        self.assertGreaterEqual(len(archive_segments), 2)
        self.assertTrue(all('archive_member_path' in segment for segment in archive_segments))
        self.assertIn('[ZIP member: docs/readme.md]', archive_segments[0]['text'])

    def test_extract_segments_uses_pdf_ocr_fallback_for_scanned_pages(self) -> None:
        """覆盖 `extract_segments_uses_pdf_ocr_fallback_for_scanned_pages` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        pdf_path = self.tmp_dir / 'scan.pdf'
        pdf_path.write_bytes(b'pdf')

        def fake_import(name: str):
            if name == 'pypdf':
                return SimpleNamespace(PdfReader=lambda path: DummyPdfReader([DummyPdfPage('native text'), DummyPdfPage('')]))
            if name == 'pdf2image':
                return SimpleNamespace(convert_from_path=lambda path, first_page, last_page, single_file: [DummyImage()])
            if name == 'PIL.ImageOps':
                return SimpleNamespace(exif_transpose=lambda image: image, autocontrast=lambda image: image)
            if name == 'pytesseract':
                return SimpleNamespace(
                    Output=SimpleNamespace(DICT='dict'),
                    image_to_data=lambda image, **kwargs: {
                        'text': ['Scanned', 'Report', '正文', '内容'],
                        'block_num': [1, 1, 1, 1],
                        'par_num': [1, 1, 1, 1],
                        'line_num': [1, 1, 2, 2],
                        'conf': ['93', '92', '91', '90'],
                    },
                    image_to_string=lambda image, **kwargs: 'scanned report',
                )
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(pdf_path)

        self.assertEqual(len(segments), 3)
        self.assertFalse(segments[0]['pdf_ocr_used'])
        self.assertEqual(segments[0]['page'], 1)
        self.assertTrue(segments[1]['pdf_ocr_used'])
        self.assertEqual(segments[1]['page'], 2)
        self.assertEqual(segments[1]['media_kind'], 'pdf_page_image')
        self.assertEqual(segments[1]['pdf_block_role'], 'heading')
        self.assertEqual(segments[1]['ocr_line_count'], 2)
        self.assertIn('标题：', segments[1]['text'])
        self.assertIn('Scanned Report', segments[1]['text'])
        self.assertTrue(segments[2]['pdf_ocr_used'])
        self.assertIn('正文块：', segments[2]['text'])
        self.assertIn('正文 内容', segments[2]['text'])

    def test_extract_segments_keeps_native_pdf_text_when_ocr_dependencies_missing(self) -> None:
        """覆盖 `extract_segments_keeps_native_pdf_text_when_ocr_dependencies_missing` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        pdf_path = self.tmp_dir / 'native.pdf'
        pdf_path.write_bytes(b'pdf')

        def fake_import(name: str):
            if name == 'pypdf':
                return SimpleNamespace(PdfReader=lambda path: DummyPdfReader([DummyPdfPage('native text'), DummyPdfPage('')]))
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(pdf_path)

        self.assertEqual(len(segments), 1)
        self.assertIn('native text', segments[0]['text'])
        self.assertFalse(segments[0]['pdf_ocr_used'])

    def test_extract_segments_optimizes_pdf_reading_order_and_preserves_blocks(self) -> None:
        """覆盖 `extract_segments_optimizes_pdf_reading_order_and_preserves_blocks` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        pdf_path = self.tmp_dir / 'layout.pdf'
        pdf_path.write_bytes(b'pdf')
        words = [
            {'text': 'QUARTERLY', 'x0': 40, 'x1': 150, 'top': 20, 'bottom': 35},
            {'text': 'REPORT', 'x0': 160, 'x1': 250, 'top': 20, 'bottom': 35},
            {'text': 'Right', 'x0': 360, 'x1': 410, 'top': 80, 'bottom': 95},
            {'text': 'column', 'x0': 415, 'x1': 470, 'top': 80, 'bottom': 95},
            {'text': 'later.', 'x0': 475, 'x1': 520, 'top': 80, 'bottom': 95},
            {'text': 'Left', 'x0': 40, 'x1': 70, 'top': 120, 'bottom': 135},
            {'text': 'column', 'x0': 75, 'x1': 130, 'top': 120, 'bottom': 135},
            {'text': 'first.', 'x0': 135, 'x1': 180, 'top': 120, 'bottom': 135},
            {'text': 'Figure', 'x0': 360, 'x1': 410, 'top': 160, 'bottom': 175},
            {'text': '1:', 'x0': 415, 'x1': 430, 'top': 160, 'bottom': 175},
            {'text': 'Trend', 'x0': 435, 'x1': 475, 'top': 160, 'bottom': 175},
            {'text': 'Q1', 'x0': 40, 'x1': 60, 'top': 220, 'bottom': 235},
            {'text': 'Revenue', 'x0': 120, 'x1': 180, 'top': 220, 'bottom': 235},
            {'text': '120', 'x0': 260, 'x1': 290, 'top': 220, 'bottom': 235},
            {'text': 'Q2', 'x0': 40, 'x1': 60, 'top': 245, 'bottom': 260},
            {'text': 'Revenue', 'x0': 120, 'x1': 180, 'top': 245, 'bottom': 260},
            {'text': '140', 'x0': 260, 'x1': 290, 'top': 245, 'bottom': 260},
        ]

        def fake_import(name: str):
            if name == 'pypdf':
                return SimpleNamespace(PdfReader=lambda path: DummyPdfReader([DummyPdfPage('fallback text')]))
            if name == 'pdfplumber':
                return SimpleNamespace(open=lambda path: DummyPdfPlumberDocument([DummyPdfPlumberPage(words)]))
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(pdf_path)

        self.assertEqual(segments[0]['pdf_block_role'], 'heading')
        self.assertEqual(segments[0]['section_title'], 'QUARTERLY REPORT')
        self.assertEqual(segments[1]['pdf_block_role'], 'body')
        self.assertIn('Left column first.', segments[1]['text'])
        self.assertIn('页面标题：QUARTERLY REPORT', segments[1]['text'])
        self.assertEqual(segments[2]['pdf_block_role'], 'table_like')
        self.assertIn('Q1 Revenue 120', segments[2]['text'])
        self.assertEqual(segments[3]['pdf_block_role'], 'body')
        self.assertIn('Right column later.', segments[3]['text'])
        self.assertEqual(segments[4]['pdf_block_role'], 'figure_caption')
        self.assertIn('Figure 1: Trend', segments[4]['text'])
        self.assertEqual([segment['pdf_block_index'] for segment in segments], [1, 2, 3, 4, 5])

    def test_extract_segments_reconstructs_pdf_table_rows(self) -> None:
        """覆盖 `extract_segments_reconstructs_pdf_table_rows` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        pdf_path = self.tmp_dir / 'table-layout.pdf'
        pdf_path.write_bytes(b'pdf')
        words = [
            {'text': 'Quarter', 'x0': 40, 'x1': 105, 'top': 40, 'bottom': 55},
            {'text': 'Revenue', 'x0': 165, 'x1': 235, 'top': 40, 'bottom': 55},
            {'text': 'Growth', 'x0': 315, 'x1': 380, 'top': 40, 'bottom': 55},
            {'text': 'Q1', 'x0': 40, 'x1': 60, 'top': 72, 'bottom': 87},
            {'text': '120', 'x0': 185, 'x1': 215, 'top': 72, 'bottom': 87},
            {'text': '15%', 'x0': 330, 'x1': 360, 'top': 72, 'bottom': 87},
            {'text': 'Q2', 'x0': 40, 'x1': 60, 'top': 102, 'bottom': 117},
            {'text': '140', 'x0': 185, 'x1': 215, 'top': 102, 'bottom': 117},
            {'text': '18%', 'x0': 330, 'x1': 360, 'top': 102, 'bottom': 117},
        ]

        def fake_import(name: str):
            if name == 'pypdf':
                return SimpleNamespace(PdfReader=lambda path: DummyPdfReader([DummyPdfPage('fallback text')]))
            if name == 'pdfplumber':
                return SimpleNamespace(open=lambda path: DummyPdfPlumberDocument([DummyPdfPlumberPage(words)]))
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(pdf_path)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]['pdf_block_role'], 'table_like')
        self.assertEqual(segments[0]['table_columns'], ['Quarter', 'Revenue', 'Growth'])
        self.assertTrue(segments[0]['table_has_header'])
        self.assertEqual(segments[0]['table_total_rows'], 2)
        self.assertIn('第1行：Quarter=Q1; Revenue=120; Growth=15%', segments[0]['text'])
        self.assertIn('| Quarter | Revenue | Growth |', segments[0]['text'])

    def test_extract_segments_detects_pdf_figure_regions_and_binds_caption(self) -> None:
        """覆盖 `extract_segments_detects_pdf_figure_regions_and_binds_caption` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        pdf_path = self.tmp_dir / 'figure-layout.pdf'
        pdf_path.write_bytes(b'pdf')
        words = [
            {'text': 'Revenue', 'x0': 40, 'x1': 100, 'top': 40, 'bottom': 55},
            {'text': 'trend', 'x0': 105, 'x1': 145, 'top': 40, 'bottom': 55},
            {'text': 'is', 'x0': 150, 'x1': 165, 'top': 40, 'bottom': 55},
            {'text': 'illustrated', 'x0': 170, 'x1': 235, 'top': 40, 'bottom': 55},
            {'text': 'below.', 'x0': 240, 'x1': 280, 'top': 40, 'bottom': 55},
            {'text': 'Figure', 'x0': 120, 'x1': 165, 'top': 270, 'bottom': 285},
            {'text': '2:', 'x0': 170, 'x1': 185, 'top': 270, 'bottom': 285},
            {'text': 'Revenue', 'x0': 190, 'x1': 255, 'top': 270, 'bottom': 285},
            {'text': 'Trend', 'x0': 260, 'x1': 305, 'top': 270, 'bottom': 285},
        ]
        images = [{'x0': 100, 'x1': 320, 'top': 90, 'bottom': 240}]

        def fake_import(name: str):
            if name == 'pypdf':
                return SimpleNamespace(PdfReader=lambda path: DummyPdfReader([DummyPdfPage('fallback text')]))
            if name == 'pdfplumber':
                return SimpleNamespace(
                    open=lambda path: DummyPdfPlumberDocument([DummyPdfPlumberPage(words, images=images)])
                )
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(pdf_path)

        figure_segments = [segment for segment in segments if segment['pdf_block_role'] == 'figure']
        self.assertEqual(len(figure_segments), 1)
        self.assertEqual(figure_segments[0]['figure_caption'], 'Figure 2: Revenue Trend')
        self.assertIn('图片说明：Figure 2: Revenue Trend', figure_segments[0]['text'])
        self.assertIn('关联正文：Revenue trend is illustrated below.', figure_segments[0]['text'])
        self.assertEqual(figure_segments[0]['figure_bbox'], '100.0,90.0,320.0,240.0')

    def test_import_path_expands_zip_into_multiple_documents(self) -> None:
        """覆盖 `import_path_expands_zip_into_multiple_documents` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        archive_path = self.tmp_dir / 'bundle.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('docs/readme.md', '# Intro\nzip content')
            archive.writestr('src/app.py', 'print("zip code")\n')

        def fake_ingest_document(doc_id: str, force: bool = False) -> dict[str, object]:
            record = self.state.documents[doc_id]
            record['status'] = 'indexed'
            return {'doc_id': doc_id, 'status': 'indexed', 'indexed_chunks': 1}

        service.ingest_document = fake_ingest_document  # type: ignore[method-assign]
        records = service.import_path(archive_path, 'demo', tags=['bundle'])

        self.assertEqual(len(records), 2)
        self.assertEqual({record['file_type'] for record in records}, {'md', 'py'})
        self.assertTrue(all('/bundle/' in record['file_path'] for record in records))
        self.assertTrue(all(record['status'] == 'indexed' for record in records))
        self.assertTrue(all(record['source_archive'] == 'bundle.zip' for record in records))
        self.assertEqual({record['archive_member_path'] for record in records}, {'docs/readme.md', 'src/app.py'})
        self.assertEqual(
            {record['archive_member_display_path'] for record in records},
            {'docs > readme.md', 'src > app.py'},
        )
        self.assertEqual(len(self.state.documents), 2)

    def test_build_documents_keeps_archive_metadata(self) -> None:
        """覆盖 `build_documents_keeps_archive_metadata` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        file_path = self.tmp_dir / 'demo' / 'bundle' / 'docs' / 'readme.md'
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text('# Intro\nzip content', encoding='utf-8')
        record = {
            'doc_id': 'doc-zip',
            'file_name': 'readme.md',
            'file_path': str(file_path),
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': ['bundle'],
            'source_archive': 'bundle.zip',
            'archive_member_path': 'docs/readme.md',
        }

        raw_segments = service._extract_segments(file_path)
        segments, metadata = service._prepare_segments(record, raw_segments)
        documents = service._build_documents({**record, **metadata}, segments)

        self.assertEqual(metadata['source_archive'], 'bundle.zip')
        self.assertEqual(metadata['archive_member_path'], 'docs/readme.md')
        self.assertEqual(metadata['archive_member_display_path'], 'docs > readme.md')
        self.assertIn('bundle.zip', metadata['document_hierarchy'])
        self.assertEqual(documents[0].metadata['source_archive'], 'bundle.zip')
        self.assertEqual(documents[0].metadata['archive_member_display_path'], 'docs > readme.md')

    def test_build_documents_adds_parent_and_title_summary_documents(self) -> None:
        """覆盖 `build_documents_adds_parent_and_title_summary_documents` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        file_path = self.tmp_dir / 'demo.md'
        file_path.write_text('# Overview\nbody text', encoding='utf-8')
        record = {
            'doc_id': 'doc-parent',
            'file_name': 'demo.md',
            'file_path': str(file_path),
            'file_type': 'md',
            'collection_name': 'demo',
            'tags': [],
            'document_title': 'Overview',
            'document_summary': 'body text',
            'document_keywords': ['overview'],
            'document_hierarchy': 'demo / Overview',
            'year': None,
            'quarter': None,
            'version': None,
            'permission': None,
            'created_at': None,
            'updated_at': None,
        }
        segments = [
            {
                'text': 'body text',
                'section_title': 'Overview',
                'hierarchy_path': 'Overview',
                'segment_summary': 'body text',
                'segment_keywords': ['body'],
                'chapter_tags': ['overview'],
                'chunking_strategy_requested': 'semantic',
                'chunking_strategy_effective': 'semantic',
                'chunking_prepared': True,
                'source_segment_count': 2,
            }
        ]

        documents = service._build_documents(record, segments)

        index_kinds = [item.metadata.get('index_kind') for item in documents]
        self.assertIn('content', index_kinds)
        self.assertIn('parent', index_kinds)
        self.assertIn('title_summary', index_kinds)
        parent_doc = next(item for item in documents if item.metadata.get('index_kind') == 'parent')
        self.assertEqual(parent_doc.id_, 'doc-parent-parent-0001')
        self.assertEqual(parent_doc.metadata['node_level'], 'parent')
        title_doc = next(item for item in documents if item.metadata.get('index_kind') == 'title_summary')
        self.assertEqual(title_doc.metadata['retrieval_target_chunk_id'], 'doc-parent-segment-0001')
        self.assertEqual(title_doc.metadata['parent_chunk_id'], 'doc-parent-parent-0001')

    def test_extract_segments_structures_table_content_for_csv_and_xlsx(self) -> None:
        """覆盖 `extract_segments_structures_table_content_for_csv_and_xlsx` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        csv_path = self.tmp_dir / 'report.csv'
        csv_path.write_text('Name,Score\nAlice,95\nBob,88\n', encoding='utf-8')
        xlsx_path = self.tmp_dir / 'report.xlsx'
        xlsx_path.write_bytes(b'placeholder')
        with patch(
            'app.rag.ingestion.importlib.import_module',
            return_value=SimpleNamespace(
                load_workbook=lambda filename, data_only, read_only: DummyWorkbook(
                    [DummyWorksheet('Sheet1', [('Name', 'Score'), ('Alice', 95)])]
                )
            ),
        ):
            csv_segments = service._extract_segments(csv_path)
            xlsx_segments = service._extract_segments(xlsx_path)

        self.assertEqual(csv_segments[0]['sheet_name'], 'report')
        self.assertEqual(csv_segments[0]['table_columns'], ['Name', 'Score'])
        self.assertTrue(csv_segments[0]['table_has_header'])
        self.assertIn('列头：Name | Score', csv_segments[0]['text'])
        self.assertIn('第1行：Name=Alice; Score=95', csv_segments[0]['text'])
        pptx_path = self.tmp_dir / 'deck.pptx'
        pptx_path.write_bytes(b'placeholder')

        def fake_import(name: str):
            if name == 'pptx':
                return SimpleNamespace(
                    Presentation=lambda path: DummyPresentation(
                        [
                            DummySlide(
                                [
                                    DummyShape('Quarterly Review', is_title=True, name='Title 1'),
                                    DummyShape('Revenue grows fast'),
                                    DummyShape('Margin improves'),
                                ],
                                notes_text='Focus on enterprise accounts',
                            )
                        ]
                    )
                )
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            pptx_segments = service._extract_segments(pptx_path)

        self.assertEqual(xlsx_segments[0]['sheet_name'], 'Sheet1')
        self.assertEqual(xlsx_segments[0]['table_columns'], ['Name', 'Score'])
        self.assertIn('第1行：Name=Alice; Score=95', xlsx_segments[0]['text'])
        self.assertEqual(pptx_segments[0]['slide_number'], 1)
        self.assertEqual(pptx_segments[0]['ppt_title'], 'Quarterly Review')
        self.assertEqual(pptx_segments[0]['ppt_body_count'], 2)
        self.assertTrue(pptx_segments[0]['ppt_has_notes'])
        self.assertIn('标题：Quarterly Review', pptx_segments[0]['text'])
        self.assertIn('- Revenue grows fast', pptx_segments[0]['text'])
        self.assertIn('备注：', pptx_segments[0]['text'])
        self.assertIn('Focus on enterprise accounts', pptx_segments[0]['text'])

    def test_extract_segments_reports_clear_errors_for_missing_office_converter_and_media_dependency(self) -> None:
        """覆盖 `extract_segments_reports_clear_errors_for_missing_office_converter_and_media_dependency` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        doc_path = self.tmp_dir / 'legacy.doc'
        doc_path.write_bytes(b'legacy')
        mp3_path = self.tmp_dir / 'meeting.mp3'
        mp3_path.write_bytes(b'fake-audio')

        with self.assertRaisesRegex(ValueError, 'LibreOffice conversion'):
            service._extract_segments(doc_path)
        with self.assertRaisesRegex(ValueError, 'openai-whisper'):
            service._extract_segments(mp3_path)

    def test_extract_segments_structures_image_ocr_lines(self) -> None:
        """覆盖 `extract_segments_structures_image_ocr_lines` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        image_path = self.tmp_dir / 'scan.png'
        image_path.write_bytes(b'fake-image')

        pytesseract_module = SimpleNamespace(
            Output=SimpleNamespace(DICT='dict'),
            image_to_data=lambda image, **kwargs: {
                'text': ['Hello', 'Team', '', 'Roadmap'],
                'block_num': [1, 1, 1, 1],
                'par_num': [1, 1, 1, 1],
                'line_num': [1, 1, 2, 2],
                'conf': ['91', '89', '-1', '88'],
            },
            image_to_string=lambda image, **kwargs: 'fallback text',
        )

        def fake_import(name: str):
            if name == 'PIL.Image':
                return SimpleNamespace(open=lambda path: DummyImage())
            if name == 'PIL.ImageOps':
                return SimpleNamespace(exif_transpose=lambda image: image, autocontrast=lambda image: image)
            if name == 'pytesseract':
                return pytesseract_module
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(image_path)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['media_kind'], 'image')
        self.assertEqual(segments[0]['ocr_line_index'], 1)
        self.assertEqual(segments[0]['image_width'], 640)
        self.assertEqual(segments[0]['image_height'], 480)
        self.assertEqual(segments[0]['ocr_line_confidence'], 90.0)
        self.assertEqual(segments[0]['ocr_line_text'], 'Hello Team')
        self.assertIn('OCR 行：1', segments[0]['text'])
        self.assertIn('内容：Hello Team', segments[0]['text'])
        self.assertEqual(segments[1]['ocr_line_text'], 'Roadmap')

    def test_extract_segments_falls_back_to_plain_image_ocr_text(self) -> None:
        """覆盖 `extract_segments_falls_back_to_plain_image_ocr_text` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        image_path = self.tmp_dir / 'plain.jpg'
        image_path.write_bytes(b'fake-image')

        pytesseract_module = SimpleNamespace(
            image_to_data=lambda image, **kwargs: [],
            image_to_string=lambda image, **kwargs: 'single block text',
        )

        def fake_import(name: str):
            if name == 'PIL.Image':
                return SimpleNamespace(open=lambda path: DummyImage(width=300, height=200))
            if name == 'PIL.ImageOps':
                return SimpleNamespace(exif_transpose=lambda image: image, autocontrast=lambda image: image)
            if name == 'pytesseract':
                return pytesseract_module
            raise ModuleNotFoundError(name)

        with patch('app.rag.ingestion.importlib.import_module', side_effect=fake_import):
            segments = service._extract_segments(image_path)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]['ocr_line_index'], 1)
        self.assertIsNone(segments[0]['ocr_line_confidence'])
        self.assertEqual(segments[0]['ocr_line_text'], 'single block text')
        self.assertIn('内容：single block text', segments[0]['text'])

    def test_extract_segments_structures_media_transcript_with_timecodes(self) -> None:
        """覆盖 `extract_segments_structures_media_transcript_with_timecodes` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        mp3_path = self.tmp_dir / 'meeting.mp3'
        mp3_path.write_bytes(b'fake-audio')

        with patch(
            'app.rag.ingestion.importlib.import_module',
            return_value=SimpleNamespace(
                load_model=lambda model_name: DummyWhisperModel(
                    {
                        'text': 'intro summary',
                        'segments': [
                            {'start': 0.0, 'end': 4.2, 'text': 'hello team'},
                            {'start': 4.2, 'end': 8.8, 'text': 'next milestone'},
                        ],
                    }
                )
            ),
        ):
            segments = service._extract_segments(mp3_path)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['media_kind'], 'audio')
        self.assertEqual(segments[0]['media_segment_index'], 1)
        self.assertEqual(segments[0]['transcript_timecode'], '00:00-00:04')
        self.assertEqual(segments[0]['transcript_text'], 'hello team')
        self.assertIn('时间范围：00:00-00:04', segments[0]['text'])
        self.assertIn('内容：hello team', segments[0]['text'])
        self.assertEqual(segments[1]['section_title'], 'meeting [00:04-00:09]')

    def test_extract_segments_falls_back_when_media_segments_missing(self) -> None:
        """覆盖 `extract_segments_falls_back_when_media_segments_missing` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        mp4_path = self.tmp_dir / 'demo.mp4'
        mp4_path.write_bytes(b'fake-video')

        with patch(
            'app.rag.ingestion.importlib.import_module',
            return_value=SimpleNamespace(
                load_model=lambda model_name: DummyWhisperModel({'text': 'single transcript'})
            ),
        ):
            segments = service._extract_segments(mp4_path)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]['media_kind'], 'video')
        self.assertEqual(segments[0]['media_segment_index'], 1)
        self.assertIsNone(segments[0]['transcript_timecode'])
        self.assertIn('内容：single transcript', segments[0]['text'])

    def test_extract_segments_converts_legacy_office_formats_via_libreoffice(self) -> None:
        """覆盖 `extract_segments_converts_legacy_office_formats_via_libreoffice` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        doc_path = self.tmp_dir / 'legacy.doc'
        doc_path.write_bytes(b'legacy')

        def fake_run(command, check, capture_output, text, timeout):
            outdir = Path(command[5])
            (outdir / 'legacy.docx').write_bytes(b'converted')
            return subprocess.CompletedProcess(command, 0, stdout='ok', stderr='')

        with (
            patch.object(service, '_resolve_office_converter_command', return_value='/usr/local/bin/soffice'),
            patch('app.rag.ingestion.subprocess.run', side_effect=fake_run),
            patch.object(service, '_read_docx', return_value=[{'text': 'converted text', 'page': None, 'section_title': None}]),
        ):
            segments = service._extract_segments(doc_path)

        self.assertEqual(segments[0]['text'], 'converted text')

    def test_legacy_office_conversion_reuses_stable_cache(self) -> None:
        """覆盖 `legacy_office_conversion_reuses_stable_cache` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        service = self._build_service()
        doc_path = self.tmp_dir / 'stable.doc'
        doc_path.write_bytes(b'stable-content')
        run_calls: list[list[str]] = []

        def fake_run(command, check, capture_output, text, timeout):
            run_calls.append(command)
            outdir = Path(command[5])
            (outdir / 'stable.docx').write_bytes(b'converted')
            return subprocess.CompletedProcess(command, 0, stdout='ok', stderr='')

        with (
            patch.object(service, '_resolve_office_converter_command', return_value='/usr/local/bin/soffice'),
            patch('app.rag.ingestion.subprocess.run', side_effect=fake_run),
            patch.object(service, '_read_docx', return_value=[{'text': 'converted text', 'page': None, 'section_title': None}]),
        ):
            service._extract_segments(doc_path)
            service._extract_segments(doc_path)

        self.assertEqual(len(run_calls), 1)

    def test_prune_converted_cache_applies_ttl_and_file_limit(self) -> None:
        """覆盖 `prune_converted_cache_applies_ttl_and_file_limit` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        settings = Settings(
            DATA_DIR=self.tmp_dir,
            CONVERTED_CACHE_TTL_SECONDS=1,
            CONVERTED_CACHE_MAX_FILES=2,
        )
        service = self._build_service(settings)
        cache_dir = service._converted_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)

        expired = cache_dir / 'expired.docx'
        keep_a = cache_dir / 'keep-a.docx'
        keep_b = cache_dir / 'keep-b.docx'
        keep_c = cache_dir / 'keep-c.docx'
        for item in (expired, keep_a, keep_b, keep_c):
            item.write_bytes(b'x')

        now = time.time()
        old = now - 10
        recent = now - 0.1
        os_times = [
            (expired, old),
            (keep_a, recent - 0.3),
            (keep_b, recent - 0.2),
            (keep_c, recent - 0.1),
        ]
        for item, timestamp in os_times:
            os.utime(item, (timestamp, timestamp))

        service._prune_converted_cache(cache_dir)

        remaining = sorted(item.name for item in cache_dir.iterdir() if item.is_file())
        self.assertEqual(remaining, ['keep-b.docx', 'keep-c.docx'])

    def test_scan_directory_returns_stats_and_structured_failures(self) -> None:
        """覆盖 `scan_directory_returns_stats_and_structured_failures` 场景，确认目标流程在当前输入、配置与依赖布置下保持稳定，并且关键输出、状态码、字段或观测结果符合预期。"""
        settings = Settings(
            DATA_DIR=self.tmp_dir,
            MAX_IMPORT_FILE_BYTES=4,
        )
        service = self._build_service(settings)
        self.state.collections['demo'] = {'name': 'demo'}
        scan_dir = self.tmp_dir / 'scan'
        scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / 'ok.md').write_text('# ok', encoding='utf-8')
        (scan_dir / 'large.txt').write_text('12345', encoding='utf-8')
        (scan_dir / 'skip.bin').write_bytes(b'123')

        with patch.object(
            service,
            'import_path',
            return_value=[
                {
                    'doc_id': 'doc-ok',
                    'file_name': 'ok.md',
                    'file_path': str(scan_dir / 'ok.md'),
                    'file_type': 'md',
                    'collection_name': 'demo',
                    'tags': [],
                    'checksum': 'x',
                    'status': 'indexed',
                    'chunk_ids': [],
                    'indexed_chunks': 1,
                    'created_at': None,
                    'updated_at': None,
                    'indexed_at': None,
                }
            ],
        ):
            result = service.scan_directory(str(scan_dir), 'demo', recursive=True, file_types=[])

        self.assertEqual(result['stats']['input_files'], 3)
        self.assertEqual(result['stats']['imported_documents'], 1)
        self.assertEqual(result['stats']['failed_files'], 1)
        self.assertEqual(result['stats']['skipped_files'], 1)
        self.assertEqual(result['failed'][0]['code'], 'file_too_large')
        self.assertEqual(result['failed'][0]['stage'], 'validation')


if __name__ == '__main__':
    unittest.main()
