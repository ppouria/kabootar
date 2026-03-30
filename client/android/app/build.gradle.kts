import com.chaquo.python.ChaquopyExtension
import java.util.Properties
import org.gradle.kotlin.dsl.configure

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

apply(plugin = "com.chaquo.python")

val versionProps = Properties()
val versionFile = rootDir.parentFile.parentFile.resolve("version.properties")
if (versionFile.isFile) {
    versionFile.inputStream().use { input -> versionProps.load(input) }
}

val appVersionName = (versionProps.getProperty("version_name") ?: "0.4.0").trim().ifBlank { "0.4.0" }
val appVersionCode = ((versionProps.getProperty("version_code") ?: "5").trim().toIntOrNull() ?: 5).coerceAtLeast(1)
val appName = (versionProps.getProperty("app_name") ?: "Kabootar").trim().ifBlank { "Kabootar" }
val releaseChannel = (versionProps.getProperty("release_channel") ?: "stable").trim().ifBlank { "stable" }
val androidBackendPort = ((project.findProperty("kabootarLocalPort") as String?)?.trim()?.toIntOrNull()
    ?: System.getenv("KABOOTAR_LOCAL_PORT")?.trim()?.toIntOrNull()
    ?: 18765).coerceIn(1024, 65535)
val buildPythonOverride = (System.getenv("KABOOTAR_BUILD_PYTHON") ?: "").trim()
val buildPythonCommand = if (buildPythonOverride.isNotBlank()) {
    listOf(buildPythonOverride)
} else if (System.getProperty("os.name").lowercase().contains("windows")) {
    listOf("py", "-3.11")
} else {
    listOf("python3")
}

val releaseKeystoreFile = ((project.findProperty("kabootarKeystoreFile") as String?)?.trim() ?: System.getenv("KABOOTAR_KEYSTORE_FILE")?.trim()).orEmpty()
val releaseKeystorePassword = ((project.findProperty("kabootarKeystorePassword") as String?)?.trim() ?: System.getenv("KABOOTAR_KEYSTORE_PASSWORD")?.trim()).orEmpty()
val releaseKeyAlias = ((project.findProperty("kabootarKeyAlias") as String?)?.trim() ?: System.getenv("KABOOTAR_KEY_ALIAS")?.trim()).orEmpty()
val releaseKeyPassword = ((project.findProperty("kabootarKeyPassword") as String?)?.trim() ?: System.getenv("KABOOTAR_KEY_PASSWORD")?.trim()).orEmpty()
val hasReleaseSigning = releaseKeystoreFile.isNotBlank() &&
    releaseKeystorePassword.isNotBlank() &&
    releaseKeyAlias.isNotBlank() &&
    releaseKeyPassword.isNotBlank()

val generatedPythonDir = layout.buildDirectory.dir("generated/python-src/main")
val syncKabootarPython by tasks.registering(Sync::class) {
    into(generatedPythonDir)
    from("../../app") {
        into("app")
        include("**/*.py")
    }
    from("../../vendor/python/persian_encoder") {
        into("persian_encoder")
    }
    from("../../frontend/templates") {
        into("kabootar_android_assets/frontend/templates")
    }
    from("../../frontend/static") {
        into("kabootar_android_assets/frontend/static")
    }
    doLast {
        val pkg = generatedPythonDir.get().file("kabootar_android_assets/__init__.py").asFile
        pkg.parentFile.mkdirs()
        if (!pkg.exists()) {
            pkg.writeText("", Charsets.UTF_8)
        }
    }
}

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
        minSdk = 24
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersionName
        ndk {
            abiFilters += listOf("arm64-v8a", "x86", "x86_64")
        }
        buildConfigField("String", "APP_VERSION_NAME", "\"${appVersionName.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("int", "APP_VERSION_CODE", appVersionCode.toString())
        buildConfigField("String", "APP_NAME", "\"${appName.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("String", "RELEASE_CHANNEL", "\"${releaseChannel.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("int", "LOCAL_BACKEND_PORT", androidBackendPort.toString())
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
            include("arm64-v8a", "x86", "x86_64")
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

configure<ChaquopyExtension> {
    defaultConfig {
        // Chaquopy-stable runtime for Android packaging
        version = "3.11"
        buildPython(*buildPythonCommand.toTypedArray())
        pip {
            install("Flask==3.0.3")
            install("SQLAlchemy==2.0.36")
            install("python-dotenv==1.0.1")
            install("requests==2.32.3")
            install("PySocks==1.7.1")
            install("dnslib==0.9.25")
            install("dnspython==2.6.1")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(generatedPythonDir.get().asFile)
        }
    }
}

tasks.named("preBuild").configure {
    dependsOn(syncKabootarPython)
}

// Gradle 8+ validation: explicitly declare dependency for Chaquopy merge tasks
// which consume generatedPythonDir from syncKabootarPython.
tasks.configureEach {
    if (name.contains("PythonSources", ignoreCase = true)) {
        dependsOn(syncKabootarPython)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("com.google.android.material:material:1.12.0")
}
