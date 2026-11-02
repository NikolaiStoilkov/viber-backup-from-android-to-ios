plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.vibertransfer.helper"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.vibertransfer.helper"
        minSdk = 26 // Android 8: toybox has sha256sum
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
// No dependencies beyond the Kotlin standard library.
