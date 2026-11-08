plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// research-env：机制实验测试应用。独立 applicationId，无第三方依赖、无网络权限、
// 不读取 local.properties、不含任何模型凭证。业务状态全部虚构，无外部副作用。
android {
    namespace = "com.lifeagent.researchenv"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.lifeagent.researchenv"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
