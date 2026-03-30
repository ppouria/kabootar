package com.kabootar.client

import android.app.Application
import android.content.Context
import android.system.Os
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.chaquo.python.android.PyApplication
import java.io.File

class KabootarApp : PyApplication() {
    companion object {
        private const val TAG = "KabootarAndroid"
        private const val KABOOTAR_PREFS = "kabootar_runtime"
        private const val STARTUP_STATE_KEY = "python_runtime_state"
        private const val STARTUP_STATE_IDLE = "idle"
        private const val STARTUP_STATE_STARTING = "starting"
        private const val STARTUP_STATE_READY = "ready"

        @Volatile
        private var runtimeStateValue = STARTUP_STATE_IDLE

        @Volatile
        private var runtimeMessageValue = "Python runtime has not started yet."

        @Synchronized
        fun ensurePythonStarted(app: Application) {
            if (Python.isStarted()) {
                runtimeStateValue = STARTUP_STATE_READY
                runtimeMessageValue = "Python runtime is ready."
                return
            }

            runtimeStateValue = STARTUP_STATE_STARTING
            runtimeMessageValue = "Starting Python runtime..."
            configurePythonEnvironment()
            prepareForStartup(app)
            Log.i(TAG, "Python.start begin thread=${Thread.currentThread().name}")
            Python.start(AndroidPlatform(app.applicationContext))
            markRuntimeReady(app)
            redirectPythonStdio()
            runtimeStateValue = STARTUP_STATE_READY
            runtimeMessageValue = "Python runtime is ready."
            Log.i(TAG, "Python.start complete thread=${Thread.currentThread().name}")
        }

        private fun runtimePrefs(app: Context) =
            app.getSharedPreferences(KABOOTAR_PREFS, Context.MODE_PRIVATE)

        private fun prepareForStartup(app: Application) {
            val prefs = runtimePrefs(app)
            val previousState = prefs.getString(STARTUP_STATE_KEY, STARTUP_STATE_IDLE).orEmpty()
            if (previousState == STARTUP_STATE_STARTING) {
                Log.w(TAG, "Previous Python startup did not complete. Purging Chaquopy runtime cache.")
                purgeChaquopyRuntime(app)
            }
            prefs.edit().putString(STARTUP_STATE_KEY, STARTUP_STATE_STARTING).apply()
        }

        private fun purgeChaquopyRuntime(app: Application) {
            val targets = listOf(
                File(app.filesDir, "chaquopy"),
                File(app.cacheDir, "chaquopy"),
                File(app.cacheDir, "AssetFinder"),
            )
            for (target in targets) {
                if (!target.exists()) {
                    continue
                }
                runCatching {
                    target.deleteRecursively()
                }.onSuccess {
                    Log.w(TAG, "Deleted stale runtime path=${target.absolutePath}")
                }.onFailure { exc ->
                    Log.w(TAG, "Failed to delete stale runtime path=${target.absolutePath}", exc)
                }
            }
            app.getSharedPreferences("chaquopy", Context.MODE_PRIVATE).edit().clear().apply()
        }

        private fun markRuntimeReady(app: Application) {
            runtimePrefs(app).edit().putString(STARTUP_STATE_KEY, STARTUP_STATE_READY).apply()
        }

        private fun configurePythonEnvironment() {
            val entries = arrayOf(
                "PYTHONDONTWRITEBYTECODE" to "1",
                "PYTHONFAULTHANDLER" to "1",
                "PYTHONNOUSERSITE" to "1",
            )
            for ((key, value) in entries) {
                try {
                    Os.setenv(key, value, true)
                    Log.i(TAG, "setenv $key=$value")
                } catch (exc: Throwable) {
                    Log.w(TAG, "setenv failed for $key", exc)
                }
            }
        }

        private fun redirectPythonStdio() {
            val platform = runCatching { Python.getPlatform() }.getOrNull()
            if (platform is AndroidPlatform) {
                runCatching { platform.redirectStdioToLogcat() }
                    .onSuccess { Log.i(TAG, "Python stdio redirected to logcat") }
                    .onFailure { exc -> Log.w(TAG, "Failed to redirect Python stdio to logcat", exc) }
            }
        }

        fun runtimeState(): String = runtimeStateValue

        fun runtimeMessage(): String = runtimeMessageValue
    }

    override fun onCreate() {
        configurePythonEnvironment()
        prepareForStartup(this)
        Log.i(TAG, "PyApplication.onCreate begin thread=${Thread.currentThread().name}")
        try {
            super.onCreate()
            markRuntimeReady(this)
            redirectPythonStdio()
            runtimeStateValue = STARTUP_STATE_READY
            runtimeMessageValue = "Python runtime is ready."
            Log.i(TAG, "PyApplication.onCreate complete thread=${Thread.currentThread().name}")
        } catch (exc: Throwable) {
            runtimeStateValue = "error"
            runtimeMessageValue = buildString {
                append(exc.javaClass.simpleName)
                val message = exc.message?.trim().orEmpty()
                if (message.isNotEmpty()) {
                    append(": ")
                    append(message)
                }
            }.ifBlank { "Python runtime startup failed." }
            Log.e(TAG, "Python runtime startup failed in Application.onCreate", exc)
            throw exc
        }
    }
}
