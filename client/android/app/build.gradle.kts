import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val versionProps = Properties()
val versionFile = rootDir.parentFile.parentFile.resolve("version.properties")
if (versionFile.isFile) {
    versionFile.inputStream().use { input -> versionProps.load(input) }
}

val appVersionName = (versionProps.getProperty("version_name") ?: "0.1.1").trim().ifBlank { "0.1.1" }
val appVersionCode = ((versionProps.getProperty("version_code") ?: "2").trim().toIntOrNull() ?: 2).coerceAtLeast(1)

val startUrlProp = (project.findProperty("startUrl") as String?)?.trim()
val startUrlEnv = System.getenv("START_URL")?.trim()
val startUrlDefault = "file:///android_asset/bootstrap.html"
val startUrlRaw = (startUrlProp ?: startUrlEnv ?: startUrlDefault).ifBlank { startUrlDefault }
val startUrlEscaped = startUrlRaw.replace("\\", "\\\\").replace("\"", "\\\"")

val releaseKeystoreFile = ((project.findProperty("kabootarKeystoreFile") as String?)?.trim() ?: System.getenv("KABOOTAR_KEYSTORE_FILE")?.trim()).orEmpty()
val releaseKeystorePassword = ((project.findProperty("kabootarKeystorePassword") as String?)?.trim() ?: System.getenv("KABOOTAR_KEYSTORE_PASSWORD")?.trim()).orEmpty()
val releaseKeyAlias = ((project.findProperty("kabootarKeyAlias") as String?)?.trim() ?: System.getenv("KABOOTAR_KEY_ALIAS")?.trim()).orEmpty()
val releaseKeyPassword = ((project.findProperty("kabootarKeyPassword") as String?)?.trim() ?: System.getenv("KABOOTAR_KEY_PASSWORD")?.trim()).orEmpty()
val hasReleaseSigning = releaseKeystoreFile.isNotBlank() &&
    releaseKeystorePassword.isNotBlank() &&
    releaseKeyAlias.isNotBlank() &&
    releaseKeyPassword.isNotBlank()

android {
    namespace = "com.kabootar.client"
    compileSdk = 35

    signingConfigs {
        if (hasReleaseSigning) {
            create("kabootarRelease") {
                storeFile = file(releaseKeystoreFile)
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    defaultConfig {
        applicationId = "com.kabootar.client"
        minSdk = 21
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersionName
        buildConfigField("String", "START_URL", "\"$startUrlEscaped\"")
        buildConfigField("String", "APP_VERSION_NAME", "\"${appVersionName.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("int", "APP_VERSION_CODE", appVersionCode.toString())
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            signingConfig = if (hasReleaseSigning) signingConfigs.getByName("kabootarRelease") else signingConfigs.getByName("debug")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    splits {
        abi {
            isEnable = true
            reset()
            include("armeabi-v7a", "arm64-v8a")
            isUniversalApk = true
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
