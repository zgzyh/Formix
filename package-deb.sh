#!/bin/bash

set -e

APP_NAME="Formix"
APP_VERSION="1.2.1"
ARCH="amd64"

BUILD_DIR="build/deb"
PKG_DIR="${BUILD_DIR}/${APP_NAME}-${APP_VERSION}"

echo "Creating DEB package for ${APP_NAME} ${APP_VERSION}..."

# 清理并创建目录
rm -rf ${BUILD_DIR}
mkdir -p ${PKG_DIR}/DEBIAN
mkdir -p ${PKG_DIR}/usr/bin
mkdir -p ${PKG_DIR}/usr/share/applications
mkdir -p ${PKG_DIR}/usr/share/icons/hicolor/256x256/apps
mkdir -p ${PKG_DIR}/opt/${APP_NAME}

# 复制 PyInstaller 生成的文件
if [ -d "dist/${APP_NAME}" ]; then
    cp -r dist/${APP_NAME}/* ${PKG_DIR}/opt/${APP_NAME}/
else
    echo "Error: dist/${APP_NAME} directory not found"
    exit 1
fi

# 创建启动脚本
cat > ${PKG_DIR}/usr/bin/formix << 'EOF'
#!/bin/bash
/opt/Formix/Formix "$@"
EOF
chmod +x ${PKG_DIR}/usr/bin/formix

# 创建桌面文件
cat > ${PKG_DIR}/usr/share/applications/formix.desktop << EOF
[Desktop Entry]
Name=Formix
Name[zh_CN]=格式转换通
Comment=A powerful format conversion tool
Comment[zh_CN]=一个强大的格式转换工具
Exec=/opt/Formix/Formix
Icon=formix
Terminal=false
Type=Application
Categories=Utility;AudioVideo;
Keywords=convert;ffmpeg;format;
EOF

# 复制图标
if [ -f "format_factory/assets/logo.ico" ]; then
    convert format_factory/assets/logo.ico -resize 256x256 ${PKG_DIR}/usr/share/icons/hicolor/256x256/apps/formix.png
fi

# 创建 DEBIAN control 文件
cat > ${PKG_DIR}/DEBIAN/control << EOF
Package: formix
Version: ${APP_VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: Formix Team
Description: Formix - Format Conversion Tool
 A powerful multimedia format conversion tool based on FFmpeg.
 支持视频、音频、图片格式转换，以及 M3U8 下载等功能。
Depends: libc6, libx11-6, libxcb1, libegl1, libopengl0
EOF

# 创建 postinst 和 prerm 脚本
cat > ${PKG_DIR}/DEBIAN/postinst << 'EOF'
#!/bin/bash
set -e
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q || true
fi
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
EOF
chmod 755 ${PKG_DIR}/DEBIAN/postinst

cat > ${PKG_DIR}/DEBIAN/prerm << 'EOF'
#!/bin/bash
set -e
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q || true
fi
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
EOF
chmod 755 ${PKG_DIR}/DEBIAN/prerm

# 设置权限
chmod -R 755 ${PKG_DIR}/DEBIAN

# 构建 DEB 包
fakeroot dpkg-deb --build ${PKG_DIR} ${BUILD_DIR}/${APP_NAME}-${APP_VERSION}-${ARCH}.deb

echo "DEB package created: ${BUILD_DIR}/${APP_NAME}-${APP_VERSION}-${ARCH}.deb"
