"""图表生成能力实现。

通过 matplotlib 生成统计图表并输出为 base64 PNG 图片。
matplotlib 为可选依赖，缺失时会给出明确提示。
"""

from __future__ import annotations

import io
import base64
from dataclasses import dataclass


@dataclass
class ChartResult:
    """图表生成结果。"""
    title: str
    chart_type: str
    image_base64: str
    description: str


class ChartCapability:
    """图表生成能力，支持柱状图、折线图、饼图、散点图等。"""

    name = 'chart'

    def __init__(self) -> None:
        self._matplotlib_available: bool | None = None

    def _check_matplotlib(self) -> None:
        if self._matplotlib_available is None:
            try:
                import matplotlib  # noqa: F401
                self._matplotlib_available = True
            except ImportError:
                self._matplotlib_available = False
        if not self._matplotlib_available:
            raise RuntimeError(
                'matplotlib is not installed. '
                'Install it with: pip install matplotlib'
            )

    def _generate_chart_image(self, fig) -> str:
        """将 matplotlib figure 转为 base64 PNG。"""
        import matplotlib
        matplotlib.use('Agg')
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        return img_base64

    def generate_bar_chart(
        self,
        labels: list[str],
        values: list[float],
        title: str = '',
        xlabel: str = '',
        ylabel: str = '',
        color: str = 'steelblue',
    ) -> ChartResult:
        """生成柱状图。

        Args:
            labels: X 轴标签。
            values: Y 轴数值。
            title: 图表标题。
            xlabel: X 轴标签名。
            ylabel: Y 轴标签名。
            color: 柱子颜色。

        Returns:
            ChartResult 包含 base64 图片。
        """
        self._check_matplotlib()
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(10, 6))
        x_pos = np.arange(len(labels))
        ax.bar(x_pos, values, color=color, alpha=0.8)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, rotation=45, ha='right')
        if title:
            ax.set_title(title, fontsize=14)
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        plt.tight_layout()

        img = self._generate_chart_image(fig)
        plt.close(fig)
        return ChartResult(
            title=title or 'Bar Chart',
            chart_type='bar',
            image_base64=img,
            description=f'Bar chart with {len(labels)} categories',
        )

    def generate_line_chart(
        self,
        x_data: list[str] | list[float],
        y_data: list[float],
        title: str = '',
        xlabel: str = '',
        ylabel: str = '',
        series_name: str = '',
    ) -> ChartResult:
        """生成折线图。

        Args:
            x_data: X 轴数据。
            y_data: Y 轴数据。
            title: 图表标题。
            xlabel: X 轴标签名。
            ylabel: Y 轴标签名。
            series_name: 数据系列名称。

        Returns:
            ChartResult 包含 base64 图片。
        """
        self._check_matplotlib()
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(x_data, y_data, marker='o', linestyle='-', label=series_name or 'data')
        if title:
            ax.set_title(title, fontsize=14)
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend()
        plt.tight_layout()

        img = self._generate_chart_image(fig)
        plt.close(fig)
        return ChartResult(
            title=title or 'Line Chart',
            chart_type='line',
            image_base64=img,
            description=f'Line chart with {len(x_data)} data points',
        )

    def generate_pie_chart(
        self,
        labels: list[str],
        values: list[float],
        title: str = '',
    ) -> ChartResult:
        """生成饼图。

        Args:
            labels: 扇形标签。
            values: 扇形数值。
            title: 图表标题。

        Returns:
            ChartResult 包含 base64 图片。
        """
        self._check_matplotlib()
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 8))
        colors = plt.cm.Set3([i / len(labels) for i in range(len(labels))])
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, autopct='%1.1f%%',
            colors=colors, startangle=90,
        )
        if title:
            ax.set_title(title, fontsize=14)
        plt.tight_layout()

        img = self._generate_chart_image(fig)
        plt.close(fig)
        return ChartResult(
            title=title or 'Pie Chart',
            chart_type='pie',
            image_base64=img,
            description=f'Pie chart with {len(labels)} segments',
        )

    def generate_scatter_chart(
        self,
        x_data: list[float],
        y_data: list[float],
        title: str = '',
        xlabel: str = '',
        ylabel: str = '',
    ) -> ChartResult:
        """生成散点图。

        Args:
            x_data: X 轴数据。
            y_data: Y 轴数据。
            title: 图表标题。
            xlabel: X 轴标签名。
            ylabel: Y 轴标签名。

        Returns:
            ChartResult 包含 base64 图片。
        """
        self._check_matplotlib()
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(x_data, y_data, alpha=0.6, s=50)
        if title:
            ax.set_title(title, fontsize=14)
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        img = self._generate_chart_image(fig)
        plt.close(fig)
        return ChartResult(
            title=title or 'Scatter Chart',
            chart_type='scatter',
            image_base64=img,
            description=f'Scatter chart with {len(x_data)} points',
        )
