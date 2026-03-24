plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val startUrlProp = (project.findProperty("startUrl") as String?)?.trim()
val startUrlEnv = System.getenv("START_URL")?.trim()
val startUrlDefault = "file:///android_asset/bootstrap.html"
val startUrlRaw = (startUrlProp ?: startUrlEnv ?: startUrlDefault).ifBlank { startUrlDefault }
val startUrlEscaped = startUrlRaw.replace("\\", "\\\\").replace("\"", "\\\"")

android {
    namespace = "com.kabootar.client"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.kabootar.client"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "START_URL", "\"$startUrlEscaped\"")
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            // CI does not ship a private keystore yet. Use the debug signing
            // config so the generated universal APK is still installable.
            signingConfig = signingConfigs.getByName("debug")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
    }

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("com.google.android.material:material:1.12.0")
}
