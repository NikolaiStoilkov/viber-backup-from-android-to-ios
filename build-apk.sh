#!/bin/bash
# Build the Android helper app (android-helper/) into
#   android-helper/app/build/outputs/apk/debug/app-debug.apk
# Needs: JDK 17, the Android SDK (platform 35 + build-tools) and Gradle (only to create the wrapper once).
set -e
cd "$(dirname "$0")/android-helper"

if [ -z "$JAVA_HOME" ] && /usr/libexec/java_home -v 17 >/dev/null 2>&1; then
  export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
fi
if [ -z "$JAVA_HOME" ] || ! "$JAVA_HOME/bin/java" -version 2>&1 | grep -q '"17'; then
  echo "JDK 17 is required by the Android Gradle Plugin."
  echo "  brew install openjdk@17"
  echo "  export JAVA_HOME=\"\$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home\""
  exit 1
fi

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
if [ ! -d "$SDK/platforms/android-35" ]; then
  echo "Android SDK platform 35 not found in $SDK. Install it with:"
  echo "  brew install --cask android-commandlinetools"
  echo "  sdkmanager --sdk_root=\"$SDK\" \"platforms;android-35\" \"build-tools;35.0.0\" \"platform-tools\""
  echo "(or install Android Studio and use its SDK Manager)"
  exit 1
fi
echo "sdk.dir=$SDK" > local.properties

if [ ! -x ./gradlew ]; then
  if ! command -v gradle >/dev/null; then
    echo "Gradle is needed once to create the wrapper:  brew install gradle"
    exit 1
  fi
  gradle wrapper --gradle-version 8.11.1
fi
./gradlew --no-daemon assembleDebug
echo
echo "Built: $(pwd)/app/build/outputs/apk/debug/app-debug.apk"
