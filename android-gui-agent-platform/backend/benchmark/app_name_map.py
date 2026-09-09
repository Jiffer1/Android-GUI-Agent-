"""App-name resolution for the AndroidWorld adapter's OPEN action.

The platform agent's OPEN action carries whatever the VLM named — a package
name, a Chinese display name ("设置"), or an English display name — while
android_world's ``open_app`` expects an English display name that prefix-
matches a regex pattern in ``android_world/env/adb_utils.py``
(``_PATTERN_TO_ACTIVITY``, via ``get_adb_activity``). Unknown names fall
through unchanged — android_world's launcher does its own fuzzy matching.

``resolve_app_name`` lookup order (feature 826):
package name -> Chinese alias -> passthrough.
"""

PACKAGE_TO_APP_NAME = {
    # System apps shipped with the Pixel 6 / API 33 AVD.
    "com.android.chrome": "chrome",
    "com.android.settings": "settings",
    "com.android.camera2": "camera",
    "com.android.dialer": "dialer",
    "com.google.android.deskclock": "clock",
    "com.google.android.contacts": "contacts",
    "com.google.android.apps.nbu.files": "files",
    # Third-party apps installed by android_world's emulator setup.
    "net.gsantner.markor": "markor",
    "net.osmand": "osmand",
    "org.videolan.vlc": "vlc",
    "com.simplemobiletools.calendar.pro": "simple calendar pro",
    "com.simplemobiletools.draw.pro": "simple draw pro",
    "com.simplemobiletools.gallery.pro": "simple gallery pro",
    "com.simplemobiletools.smsmessages": "simple sms messenger",
    "com.dimowner.audiorecorder": "audio recorder",
    # AndroidWorld custom APKs — authoritative package names from
    # android_world/env/adb_utils.py `_PATTERN_TO_ACTIVITY`.
    "com.arduia.expense": "pro expense",
    "com.flauschcode.broccoli": "broccoli",
    "org.tasks": "tasks",
}

# Common Chinese display names the VLM outputs for OPEN (smoke-run evidence:
# 设置/相机/文件管理器 loops killed 4 tasks in the 824 baseline). Values must
# prefix-match an official `_PATTERN_TO_ACTIVITY` pattern (lowercase ASCII).
CHINESE_ALIAS = {
    "设置": "settings", "系统设置": "settings",
    "相机": "camera", "照相机": "camera",
    "文件管理器": "files", "文件": "files",
    "时钟": "clock", "闹钟": "clock",
    "电话": "dialer", "拨号": "dialer", "拨号器": "dialer",
    "通讯录": "contacts", "联系人": "contacts",
    "短信": "simple sms messenger", "信息": "simple sms messenger",
    "日历": "simple calendar pro", "简单日历": "simple calendar pro",
    "图库": "simple gallery pro", "相册": "simple gallery pro",
    "画廊": "simple gallery pro", "照片": "simple gallery pro",
    "录音机": "audio recorder", "录音": "audio recorder",
    "浏览器": "chrome", "谷歌浏览器": "chrome",
    "地图": "osmand", "导航": "osmand",
    "笔记": "markor", "记事本": "markor",
    "记账": "pro expense", "记账本": "pro expense",
    "支出": "pro expense", "花销": "pro expense",
    "食谱": "broccoli", "菜谱": "broccoli",
    "任务": "tasks", "待办": "tasks", "待办事项": "tasks",
    "画板": "simple draw pro", "画图": "simple draw pro", "绘图": "simple draw pro",
    "视频播放器": "vlc", "播放器": "vlc",
}


def resolve_app_name(raw: str) -> str:
    """Map a VLM-provided app name to an android_world display name.

    Lookup order: package name (case-insensitive) -> Chinese alias ->
    passthrough of the stripped input.
    """
    key = (raw or "").strip()
    return (
        PACKAGE_TO_APP_NAME.get(key.lower())
        or CHINESE_ALIAS.get(key)
        or CHINESE_ALIAS.get(key.lower())
        or key
    )
