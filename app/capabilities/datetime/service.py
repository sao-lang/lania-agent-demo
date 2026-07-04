"""时间日期能力实现。

提供当前时间、日期信息、时区转换等本地时间能力。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


# 常见时区偏移（小时）
_TIMEZONE_OFFSETS: dict[str, timedelta] = {
    'UTC': timedelta(hours=0),
    'GMT': timedelta(hours=0),
    'CST': timedelta(hours=8),       # 中国标准时间
    'EST': timedelta(hours=-5),      # 美国东部标准时间
    'EDT': timedelta(hours=-4),      # 美国东部夏令时间
    'PST': timedelta(hours=-8),      # 美国太平洋标准时间
    'PDT': timedelta(hours=-7),      # 美国太平洋夏令时间
    'JST': timedelta(hours=9),       # 日本标准时间
    'IST': timedelta(hours=5, minutes=30),  # 印度标准时间
    'BST': timedelta(hours=1),       # 英国夏令时间
    'CET': timedelta(hours=1),       # 欧洲中部时间
    'EET': timedelta(hours=2),       # 欧洲东部时间
    'AEST': timedelta(hours=10),     # 澳大利亚东部标准时间
    'AEDT': timedelta(hours=11),     # 澳大利亚东部夏令时间
    'BRT': timedelta(hours=-3),      # 巴西利亚时间
    'HKT': timedelta(hours=8),       # 香港时间
    'SGT': timedelta(hours=8),       # 新加坡时间
}

# 中文城市到时区名映射
_CITY_TZ: dict[str, str] = {
    '北京': 'CST', '上海': 'CST', '广州': 'CST', '深圳': 'CST',
    '东京': 'JST', '首尔': 'JST', '大阪': 'JST',
    '伦敦': 'BST', '巴黎': 'CET', '柏林': 'CET',
    '纽约': 'EST', '洛杉矶': 'PST', '芝加哥': 'CST',
    '悉尼': 'AEST', '墨尔本': 'AEDT',
    '香港': 'HKT', '新加坡': 'SGT',
}


@dataclass
class TimeInfo:
    """时间信息。"""
    timezone: str
    datetime_str: str
    hour: int
    minute: int
    second: int
    weekday: int  # 0=周一, 6=周日
    weekday_name: str


@dataclass
class DateInfo:
    """日期信息。"""
    date_str: str
    year: int
    month: int
    day: int
    weekday: int
    weekday_name: str
    is_leap_year: bool
    day_of_year: int
    days_in_month: int


class DateTimeCapability:
    """时间日期能力，支持获取当前时间和日期信息。"""

    name = 'datetime'

    _WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    _WEEKDAY_NAMES_EN = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    @staticmethod
    def _resolve_timezone(timezone_or_city: str) -> str:
        """解析时区或城市名称，返回标准时区缩写。"""
        key = timezone_or_city.strip()
        if key in _CITY_TZ:
            return _CITY_TZ[key]
        if key.upper() in _TIMEZONE_OFFSETS:
            return key.upper()
        return 'UTC'

    def get_current_time(self, timezone_or_city: str = 'UTC') -> TimeInfo:
        """获取指定时区或城市的当前时间。

        Args:
            timezone_or_city: 时区缩写（如 CST, EST）或城市名（如 北京, 纽约）。

        Returns:
            TimeInfo 时间信息。
        """
        tz_name = self._resolve_timezone(timezone_or_city)
        offset = _TIMEZONE_OFFSETS.get(tz_name, timedelta(hours=0))
        now = datetime.now(timezone.utc) + offset
        wd = now.weekday()  # 0=周一
        return TimeInfo(
            timezone=tz_name,
            datetime_str=now.strftime('%Y-%m-%d %H:%M:%S'),
            hour=now.hour,
            minute=now.minute,
            second=now.second,
            weekday=wd,
            weekday_name=self._WEEKDAY_NAMES[wd],
        )

    def get_date_info(self, date_str: str = '') -> DateInfo:
        """获取指定日期的详细信息。

        Args:
            date_str: 日期字符串（如 "2026-07-04"），为空则返回今天。

        Returns:
            DateInfo 日期信息。
        """
        if date_str:
            dt = datetime.strptime(date_str.strip(), '%Y-%m-%d')
        else:
            dt = datetime.now(timezone.utc)

        year = dt.year
        month = dt.month

        # 该月天数
        if month == 2:
            days = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
        elif month in (4, 6, 9, 11):
            days = 30
        else:
            days = 31

        wd = dt.weekday()
        return DateInfo(
            date_str=dt.strftime('%Y-%m-%d'),
            year=year,
            month=month,
            day=dt.day,
            weekday=wd,
            weekday_name=self._WEEKDAY_NAMES[wd],
            is_leap_year=(year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)),
            day_of_year=dt.timetuple().tm_yday,
            days_in_month=days,
        )
