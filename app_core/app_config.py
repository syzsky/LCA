"""
应用程序配置信息
"""

APP_NAME = "LCA"
APP_VERSION = "1.2.6.3"

# 更新服务器配置
# 开源版不内置私有更新地址，请通过环境或发行配置替换。
UPDATE_SERVER = "https://example.invalid/updates"
MANIFEST_URL = f"{UPDATE_SERVER}/manifest.json"
INSTALLER_URL_TEMPLATE = f"{UPDATE_SERVER}/LCA_Setup_v{{version}}.exe"

# 安全配置
VERIFY_HASH = True  # 是否验证文件哈希
VERIFY_SIZE = True  # 是否验证文件大小
