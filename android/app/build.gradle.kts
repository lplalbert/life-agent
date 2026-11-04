import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 凭证与端点来自 local.properties（不入 git），与 Python 版 .env 同源配置
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.lifeagent.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.lifeagent.app"
        minSdk = 30          // AccessibilityService.takeScreenshot 需要 API 30
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"

        buildConfigField("String", "BASE_URL",
            "\"" + localProps.getProperty("lifeagent.base.url", "https://token-plan-cn.xiaomimimo.com/anthropic") + "\"")
        buildConfigField("String", "AUTH_TOKEN",
            "\"" + localProps.getProperty("lifeagent.auth.token", "") + "\"")
        buildConfigField("String", "MODEL",
            "\"" + localProps.getProperty("lifeagent.model", "mimo-v2.6-pro") + "\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
}
