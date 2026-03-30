package com.kabootar.client

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceError
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.chaquo.python.Python
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    companion object {
        private const val BOOTSTRAP_URL = "file:///android_asset/bootstrap.html"
        private const val TAG = "KabootarAndroid"
    }

    private lateinit var rootView: FrameLayout
    private lateinit var webView: WebView
    private val backendExecutor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "kabootar-backend-1").apply {
            isDaemon = true
        }
    }
    @Volatile
    private var backendState = "idle"
    @Volatile
    private var backendMessage = "Local backend has not started yet."
    @Volatile
    private var backendUrl = "http://127.0.0.1:${BuildConfig.LOCAL_BACKEND_PORT}"
    @Volatile
    private var backendStarting = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureEdgeToEdge()
        setContentView(R.layout.activity_main)

        rootView = findViewById(R.id.rootView)
        webView = findViewById(R.id.webView)
        applyWindowInsets()
        webView.setBackgroundColor(Color.TRANSPARENT)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            builtInZoomControls = false
            displayZoomControls = false
            allowFileAccess = true
            allowContentAccess = true
        }

        webView.addJavascriptInterface(AndroidBridge(), "KabootarAndroid")
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            private fun handleMainFrameError(failingUrl: String?) {
                if (!failingUrl.isNullOrBlank() && failingUrl.startsWith(BOOTSTRAP_URL)) {
                    return
                }
                backendState = "error"
                backendMessage = "Unable to load the local Kabootar endpoint."
                Toast.makeText(
                    this@MainActivity,
                    "Unable to load local backend",
                    Toast.LENGTH_LONG,
                ).show()
                loadBootstrap()
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    handleMainFrameError(request.url?.toString())
                }
            }

            @Deprecated("Deprecated in Java")
            override fun onReceivedError(
                view: WebView?,
                errorCode: Int,
                description: String?,
                failingUrl: String?,
            ) {
                handleMainFrameError(failingUrl)
            }
        }

        loadBootstrap()
        Log.i(
            TAG,
            "MainActivity.onCreate version=${BuildConfig.APP_VERSION_NAME} thread=${Thread.currentThread().name} pythonStarted=${Python.isStarted()} runtimeState=${KabootarApp.runtimeState()}",
        )
        startLocalBackend(false)
    }

    private fun configureEdgeToEdge() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val attrs = window.attributes
            attrs.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            window.attributes = attrs
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.isNavigationBarContrastEnforced = false
        }
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
    }

    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(rootView) { view, windowInsets ->
            val insetTypes = WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            val systemInsets = windowInsets.getInsets(insetTypes)
            view.setPadding(systemInsets.left, systemInsets.top, systemInsets.right, systemInsets.bottom)
            WindowInsetsCompat.Builder(windowInsets)
                .setInsets(insetTypes, Insets.NONE)
                .build()
        }
        ViewCompat.requestApplyInsets(rootView)
    }

    private fun loadBootstrap() {
        webView.loadUrl(BOOTSTRAP_URL)
    }

    private fun openLocalBackend() {
        if (backendUrl.isNotBlank()) {
            webView.loadUrl(backendUrl)
        }
    }

    private fun startLocalBackend(force: Boolean) {
        if (backendStarting) {
            return
        }
        backendStarting = true
        backendState = "starting"
        backendMessage = if (force) {
            "Retrying local backend startup..."
        } else {
            "Starting local backend..."
        }

        try {
            Log.i(
                TAG,
                "startLocalBackend force=$force thread=${Thread.currentThread().name} pythonStarted=${Python.isStarted()} runtimeState=${KabootarApp.runtimeState()}",
            )
            KabootarApp.ensurePythonStarted(application)
        } catch (exc: Throwable) {
            Log.e(TAG, "Python runtime startup failed before backend execution", exc)
            backendState = "error"
            backendMessage = KabootarApp.runtimeMessage()
            loadBootstrap()
            Toast.makeText(this, "Python runtime failed to start", Toast.LENGTH_LONG).show()
            backendStarting = false
            return
        }

        backendExecutor.execute {
            try {
                Log.i(
                    TAG,
                    "backend executor entered thread=${Thread.currentThread().name} pythonStarted=${Python.isStarted()} runtimeState=${KabootarApp.runtimeState()}",
                )
                if (!Python.isStarted()) {
                    error("Python runtime is not initialized. Application startup did not complete.")
                }
                val runtime = Python.getInstance().getModule("kabootar_android.runtime")
                val url = runtime.callAttr(
                    "start_backend",
                    filesDir.absolutePath,
                    cacheDir.absolutePath,
                    BuildConfig.LOCAL_BACKEND_PORT,
                    BuildConfig.APP_NAME,
                    BuildConfig.APP_VERSION_NAME,
                    BuildConfig.APP_VERSION_CODE,
                    BuildConfig.RELEASE_CHANNEL,
                ).toString()

                backendUrl = url
                backendState = "ready"
                backendMessage = "Local backend is ready."

                runOnUiThread {
                    openLocalBackend()
                }
            } catch (exc: Throwable) {
                Log.e(TAG, "Local backend startup failed", exc)
                backendState = "error"
                backendMessage = buildString {
                    append(exc.javaClass.simpleName)
                    val msg = exc.message?.trim().orEmpty()
                    if (msg.isNotEmpty()) {
                        append(": ")
                        append(msg)
                    }
                }.ifBlank { "Unknown backend startup error" }
                runOnUiThread {
                    loadBootstrap()
                    Toast.makeText(this, "Local backend failed to start", Toast.LENGTH_LONG).show()
                }
            } finally {
                backendStarting = false
            }
        }
    }

    inner class AndroidBridge {
        @JavascriptInterface
        fun getBackendState(): String = backendState

        @JavascriptInterface
        fun getBackendMessage(): String = backendMessage

        @JavascriptInterface
        fun getBackendUrl(): String = backendUrl

        @JavascriptInterface
        fun openLocalBackend() {
            runOnUiThread {
                if (backendState == "ready") {
                    this@MainActivity.openLocalBackend()
                } else {
                    this@MainActivity.startLocalBackend(true)
                }
            }
        }

        @JavascriptInterface
        fun retryBackend() {
            runOnUiThread {
                this@MainActivity.loadBootstrap()
                this@MainActivity.startLocalBackend(true)
            }
        }

        @JavascriptInterface
        fun showToast(message: String) {
            runOnUiThread {
                Toast.makeText(this@MainActivity, message, Toast.LENGTH_LONG).show()
            }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) {
            webView.goBack()
            return
        }
        super.onBackPressed()
    }

    override fun onDestroy() {
        super.onDestroy()
        backendExecutor.shutdownNow()
    }
}
