#!/usr/bin/env bash
# Every construct the Bash spec claims, once.

readonly RELEASE_TAG=v1
BUILD_DIR=out

build() {
  local target="$1"
  echo "building ${target}"
}

deploy() {
  build "$RELEASE_TAG"
}
