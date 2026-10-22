import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

val firebaseAndroidConfig = providers.environmentVariable(
    "CCB_MOBILE_FIREBASE_ANDROID_CONFIG",
).orNull?.trim()
val googleServicesFile = layout.projectDirectory.file("google-services.json").asFile
if (!firebaseAndroidConfig.isNullOrEmpty()) {
    val source = file(firebaseAndroidConfig)
    if (!source.isFile) {
        throw GradleException(
            "CCB_MOBILE_FIREBASE_ANDROID_CONFIG must point to a readable " +
                "deployment-owned google-services.json file."
        )
    }
    if (source.canonicalFile != googleServicesFile.canonicalFile) {
        googleServicesFile.writeBytes(source.readBytes())
    }
    apply(plugin = "com.google.gms.google-services")
} else if (googleServicesFile.isFile) {
    apply(plugin = "com.google.gms.google-services")
}

val releaseSigningPropertiesFile = rootProject.file("release-signing.properties")
val releaseSigningProperties = Properties().apply {
    if (releaseSigningPropertiesFile.isFile) {
        releaseSigningPropertiesFile.inputStream().use { load(it) }
    }
}

fun releaseSigningValue(propertyName: String, environmentName: String): String? {
    return providers.environmentVariable(environmentName).orNull
        ?: releaseSigningProperties.getProperty(propertyName)
}

val releaseStoreFile = releaseSigningValue(
    "storeFile",
    "CCB_MOBILE_RELEASE_STORE_FILE",
)
val releaseStorePassword = releaseSigningValue(
    "storePassword",
    "CCB_MOBILE_RELEASE_STORE_PASSWORD",
)
val releaseKeyAlias = releaseSigningValue(
    "keyAlias",
    "CCB_MOBILE_RELEASE_KEY_ALIAS",
)
val releaseKeyPassword = releaseSigningValue(
    "keyPassword",
    "CCB_MOBILE_RELEASE_KEY_PASSWORD",
)
val hasReleaseSigningConfig = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { !it.isNullOrBlank() }

android {
    namespace = "io.ccb.mobile.ccb_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "io.ccb.mobile.ccb_mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (hasReleaseSigningConfig) {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            if (providers.environmentVariable("CCB_MOBILE_TEST_BUILD").orNull == "1") {
                applicationIdSuffix = ".relaytest"
                versionNameSuffix = "-relay-test"
            }
        }
        release {
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            if (hasReleaseSigningConfig) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }
}

gradle.taskGraph.whenReady {
    val releaseRequested = allTasks.any { task ->
        task.name.contains("Release") || task.path.contains("Release")
    }
    if (releaseRequested && !hasReleaseSigningConfig) {
        throw GradleException(
            "CCB Mobile release signing is required for release builds. " +
                "Set CCB_MOBILE_RELEASE_STORE_FILE, " +
                "CCB_MOBILE_RELEASE_STORE_PASSWORD, " +
                "CCB_MOBILE_RELEASE_KEY_ALIAS, and " +
                "CCB_MOBILE_RELEASE_KEY_PASSWORD, or create " +
                "app/android/release-signing.properties from " +
                "release-signing.properties.example. Release builds are not " +
                "signed with the debug key."
        )
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
