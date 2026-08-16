# =============================================================================
# RAAG — single-image build
#
# Two stages: compile the Sample Engine in a full build environment, then copy
# only the resulting binary into a slim Python runtime. The build stage's
# compiler toolchain, CMake cache, and Tree-sitter checkouts never reach the
# final image — the difference is roughly a gigabyte, and none of it is needed
# to actually run `raag`.
# =============================================================================

# --- Stage 1: compile the Sample Engine -------------------------------------
FROM ubuntu:24.04 AS cpp-build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY sample_engine/ ./sample_engine/

# Release build: this binary does the actual parsing work, and a Debug build
# is several times slower for no benefit once compiled once here.
RUN cmake -S sample_engine -B build -DCMAKE_BUILD_TYPE=Release -DRAAG_BUILD_TESTS=OFF \
    && cmake --build build --parallel

# --- Stage 2: runtime ---------------------------------------------------------
FROM python:3.12-slim AS runtime

# libstdc++ is needed at runtime for the copied binary even though nothing
# else in this image compiles C++.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=cpp-build /src/build/raag_sample /usr/local/bin/raag_sample

COPY pyproject.toml requirements.txt ./
COPY tune_engine/ ./tune_engine/
COPY master_engine/ ./master_engine/
COPY cli/ ./cli/

RUN pip install --no-cache-dir --break-system-packages -e .

ENTRYPOINT ["raag"]
CMD ["--help"]
