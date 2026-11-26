# The Linux build computer for PlanetPDF (2026-10-26), for x86-64 or ARM64:
#
#     .venv/bin/python scripts/build_linux.py --docker --arch arm64
#
# Ubuntu 24.04 (glibc 2.39: the oldest system Qt's ARM64 build runs on), Python 3.14 and
# Ghostscript built from their sources (so the program doesn't depend on the system's), the
# packages PlanetPDF needs from requirements*.txt, and the tools that make the AppImage, .deb
# and .rpm. Every download is checked against its SHA-256.
FROM ubuntu:24.04

ARG TARGETARCH
ARG PYTHON_VERSION=3.14.8
ARG PYTHON_SHA256=c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73
ARG GS_VERSION=10.08.0
ARG GS_SHA256=c20492bc8ebb96c87fa2e52a0926e1cda8cde95d66145e018ac713fed5da38cf
ARG PORTAUDIO_VERSION=19.7.0
ARG PORTAUDIO_SHA256=5af29ba58bbdbb7bbcefaaecc77ec8fc413f0db6f4c4e286c40c3e1b83174fa0
ARG RUNTIME_RELEASE=20251108
ARG RUNTIME_SHA256_ARM64=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444
ARG RUNTIME_SHA256_AMD64=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils build-essential pkg-config binutils file patchelf \
        libssl-dev libffi-dev zlib1g-dev libbz2-dev liblzma-dev libsqlite3-dev libreadline-dev \
        uuid-dev libncurses-dev libgdbm-dev libzstd-dev libasound2-dev \
        squashfs-tools rpm dpkg-dev \
        tesseract-ocr-eng tesseract-ocr-osd \
        libgl1 libegl1 libfontconfig1 libfreetype6 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 \
        libglib2.0-0t64 libx11-xcb1 libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
        libxcb-xkb1 libxcb-util1 libsm6 libice6 libcups2t64 libportaudio2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Python, shared (PyInstaller needs libpython)
RUN curl -fsSLo /tmp/python.tar.xz https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz \
    && echo "${PYTHON_SHA256}  /tmp/python.tar.xz" | sha256sum -c - \
    && mkdir /tmp/python && tar -xJf /tmp/python.tar.xz -C /tmp/python --strip-components=1 \
    && cd /tmp/python \
    && ./configure --prefix=/opt/python --enable-shared LDFLAGS=-Wl,-rpath=/opt/python/lib --with-ensurepip=install \
    && make -j"$(nproc)" && make install \
    && cd / && rm -rf /tmp/python /tmp/python.tar.xz

# Ghostscript with its own libraries and its resources compiled in (needs only the C library);
# the color profiles and resources are also installed as files: PlanetPDF hands Ghostscript the
# sRGB / CMYK profile by its path (PDF/A, PDF/X)
RUN curl -fsSLo /tmp/gs.tar.xz https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs$(echo ${GS_VERSION} | tr -d .)/ghostscript-${GS_VERSION}.tar.xz \
    && echo "${GS_SHA256}  /tmp/gs.tar.xz" | sha256sum -c - \
    && mkdir /tmp/gs && tar -xJf /tmp/gs.tar.xz -C /tmp/gs --strip-components=1 \
    && cd /tmp/gs \
    && ./configure --prefix=/opt/gs --without-x --disable-cups --disable-dbus --disable-gtk \
        --disable-fontconfig --without-libidn --without-libpaper --without-tesseract \
    && make -j"$(nproc)" && make install \
    && cp -r iccprofiles Resource /opt/gs/share/ghostscript/${GS_VERSION}/ \
    && mkdir -p /opt/gs/licenses && cp LICENSE doc/COPYING /opt/gs/licenses/ \
    && cd / && rm -rf /tmp/gs /tmp/gs.tar.xz

# PortAudio (Read Out Loud's sound) with ALSA only: the system's copy also needs JACK, which most
# desktops don't have; ALSA reaches PipeWire and PulseAudio too
RUN curl -fsSLo /tmp/pa.tar.gz https://github.com/PortAudio/portaudio/archive/refs/tags/v${PORTAUDIO_VERSION}.tar.gz \
    && echo "${PORTAUDIO_SHA256}  /tmp/pa.tar.gz" | sha256sum -c - \
    && mkdir /tmp/pa && tar -xzf /tmp/pa.tar.gz -C /tmp/pa --strip-components=1 \
    && cd /tmp/pa \
    && ./configure --prefix=/opt/portaudio --with-alsa --without-jack --without-oss \
    && make -j"$(nproc)" && make install \
    && mkdir -p /opt/portaudio/licenses && cp LICENSE.txt /opt/portaudio/licenses/ \
    && cd / && rm -rf /tmp/pa /tmp/pa.tar.gz

# The AppImage runtime (the small program at the start of every AppImage)
RUN case "${TARGETARCH}" in \
        arm64) name=aarch64; sum=${RUNTIME_SHA256_ARM64} ;; \
        amd64) name=x86_64; sum=${RUNTIME_SHA256_AMD64} ;; \
        *) echo "unsupported: ${TARGETARCH}"; exit 1 ;; \
    esac \
    && curl -fsSLo /opt/appimage-runtime https://github.com/AppImage/type2-runtime/releases/download/${RUNTIME_RELEASE}/runtime-${name} \
    && echo "${sum}  /opt/appimage-runtime" | sha256sum -c -

# PlanetPDF's packages (the same pins as everywhere else)
COPY requirements.txt requirements-dev.txt /tmp/req/
RUN /opt/python/bin/python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --require-hashes -r /tmp/req/requirements-dev.txt \
    && rm -rf /tmp/req

ENV PATH=/opt/venv/bin:/opt/python/bin:$PATH PYTHONDONTWRITEBYTECODE=1

# The apt / dnf repositories (2026-10-27): signing (gpg, rpmsign) and their indexes
RUN apt-get update && apt-get install -y --no-install-recommends gnupg apt-utils createrepo-c \
    && rm -rf /var/lib/apt/lists/*
