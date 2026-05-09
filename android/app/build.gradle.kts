plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "co.voci"
    compileSdk = 35

    defaultConfig {
        applicationId = "co.voci"
        minSdk = 29  // AudioPlaybackCaptureConfiguration requires Android 10
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    sourceSets["main"].java.srcDirs("src/main/kotlin")

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")

    // Networking — OkHttp speaks Deepgram's WebSocket protocol directly + MyMemory HTTP
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // JSON parsing for Deepgram messages and MyMemory responses
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlin:kotlin-stdlib")

    // Coroutines for service lifecycle + audio loop
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
