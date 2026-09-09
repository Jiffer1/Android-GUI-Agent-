"""Contract tests: benchmark app-name resolution (feature 826, FR-01).

``resolve_app_name`` lookup order: package name -> Chinese alias ->
passthrough. Pure dict contracts — no android_world dependency here (values
are validated against the official ``_PATTERN_TO_ACTIVITY`` patterns once in
the venv313 environment during implementation).
"""
import pytest

from benchmark.app_name_map import (
    CHINESE_ALIAS,
    PACKAGE_TO_APP_NAME,
    resolve_app_name,
)


class TestPackageNameResolution:
    def test_known_package_resolves_case_insensitive(self):
        assert resolve_app_name("com.android.chrome") == "chrome"
        assert resolve_app_name("Com.Android.Settings") == "settings"

    @pytest.mark.parametrize(
        "package,expected",
        [
            ("com.arduia.expense", "pro expense"),
            ("com.flauschcode.broccoli", "broccoli"),
            ("org.tasks", "tasks"),
            ("net.osmand", "osmand"),
        ],
    )
    def test_corrected_custom_apk_packages(self, package, expected):
        assert resolve_app_name(package) == expected

    @pytest.mark.parametrize(
        "legacy",
        ["com.android.expense", "me.zebao.recipe", "com.kinandcasus.tasket"],
    )
    def test_wrong_legacy_packages_no_longer_resolve(self, legacy):
        # 824-era wrong keys must not linger as hidden aliases.
        assert resolve_app_name(legacy) == legacy


class TestChineseAliasResolution:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("设置", "settings"),
            ("系统设置", "settings"),
            ("相机", "camera"),
            ("文件管理器", "files"),
            ("时钟", "clock"),
            ("电话", "dialer"),
            ("通讯录", "contacts"),
            ("联系人", "contacts"),
            ("短信", "simple sms messenger"),
            ("日历", "simple calendar pro"),
            ("图库", "simple gallery pro"),
            ("录音机", "audio recorder"),
            ("浏览器", "chrome"),
            ("地图", "osmand"),
            ("笔记", "markor"),
            ("记账", "pro expense"),
            ("食谱", "broccoli"),
            ("任务", "tasks"),
            ("画板", "simple draw pro"),
            ("视频播放器", "vlc"),
        ],
    )
    def test_common_chinese_names_resolve(self, raw, expected):
        assert resolve_app_name(raw) == expected

    def test_alias_values_are_official_ascii_names(self):
        # Structural guard: values are non-empty, stripped, lowercase ASCII
        # official display names (prefix-matchable by android_world).
        assert all(
            v and v.isascii() and v == v.lower() and v.strip() == v
            for v in CHINESE_ALIAS.values()
        )
        # Every Chinese alias key must actually be Chinese / non-ASCII.
        assert all(not k.isascii() for k in CHINESE_ALIAS)


class TestResolveOrder:
    def test_priority_package_then_alias_then_passthrough(self):
        assert resolve_app_name("com.android.chrome") == "chrome"
        assert resolve_app_name("设置") == "settings"
        assert resolve_app_name("some unknown app") == "some unknown app"

    def test_whitespace_stripped(self):
        assert resolve_app_name("  设置 ") == "settings"

    def test_tables_are_disjoint(self):
        # A package key must never double as a Chinese alias key.
        assert not set(PACKAGE_TO_APP_NAME) & set(CHINESE_ALIAS)
