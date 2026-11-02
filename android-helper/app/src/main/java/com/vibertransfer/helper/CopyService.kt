package com.vibertransfer.helper

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException

/**
 * Foreground service that does the root copy.
 *
 * 1. `su -c id` – this is what makes Magisk/SuperSU show its "Grant root?" prompt.
 * 2. `su -c sh copy.sh` – runs res/raw/copy.sh, which copies Viber's data into
 *    /data/local/tmp/vibertransfer_staging and writes checksums.
 * Progress is written to status.json (see [Status]) which the Mac polls over adb.
 */
class CopyService : Service() {
    companion object {
        const val ACTION_START = "com.vibertransfer.helper.START"
        const val ACTION_CANCEL = "com.vibertransfer.helper.CANCEL_COPY"
        const val ACTION_CLEANUP = "com.vibertransfer.helper.CLEANUP_STAGING"
        const val EXTRA_RUN_ID = "run_id"
        const val STAGING = "/data/local/tmp/vibertransfer_staging"
        private const val CHANNEL = "transfer"

        @Volatile
        var isRunning = false
    }

    @Volatile private var proc: Process? = null
    @Volatile private var cancelled = false
    @Volatile private var scriptPid: String? = null
    private var runId = ""
    private var filesDone = 0
    private var filesTotal = 0
    private val found = JSONArray()
    private val warnings = JSONArray()
    private var lastWrite = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                goForeground()
                if (!isRunning) {
                    isRunning = true
                    cancelled = false
                    runId = intent.getStringExtra(EXTRA_RUN_ID) ?: ""
                    Thread { work() }.start()
                }
            }
            ACTION_CANCEL -> {
                cancelled = true
                Thread {
                    // Stop the root shell itself; destroying our su client alone may leave it running.
                    scriptPid?.let { runSu("pkill -P $it; kill $it") }
                    proc?.destroy()
                    if (!isRunning) stopSelf()
                }.start()
            }
            ACTION_CLEANUP -> Thread {
                if (!isRunning) {
                    runSu("rm -rf $STAGING")
                    stopSelf()
                }
            }.start()
        }
        return START_NOT_STICKY
    }

    private fun goForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Transfer", NotificationManager.IMPORTANCE_LOW))
        val n = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle("Viber Transfer Helper")
            .setContentText("Copying Viber data for your Mac…")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(1, n)
        }
    }

    private fun work() {
        try {
            update("requesting_root", "Waiting for root permission…", force = true)
            if (!checkRoot()) return
            update("copying", "Copying Viber data…", force = true)
            copy()
        } catch (e: Exception) {
            update("error", e.message ?: e.toString(), force = true)
        } finally {
            isRunning = false
            proc = null
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    /** Asks for root. Magisk/SuperSU show their prompt here. */
    private fun checkRoot(): Boolean {
        val p = try {
            ProcessBuilder("su", "-c", "id").redirectErrorStream(true).start()
        } catch (e: IOException) {
            update("no_root", "This phone is not rooted (no su binary found).", force = true)
            return false
        }
        proc = p
        val out = p.inputStream.bufferedReader().readText()
        p.waitFor()
        if (out.contains("uid=0")) return true
        update("root_denied", "Root permission was not granted. Open Magisk › Superuser and allow " +
            "Viber Transfer Helper, then try again.", force = true)
        return false
    }

    private fun copy() {
        // Write the script to our private cache dir; root can read it from there.
        val script = File(cacheDir, "copy.sh")
        resources.openRawResource(R.raw.copy).use { input -> script.outputStream().use { input.copyTo(it) } }

        val p = ProcessBuilder("su", "-c", "sh '${script.absolutePath}'").redirectErrorStream(true).start()
        proc = p
        var done = false
        val tail = ArrayDeque<String>()
        p.inputStream.bufferedReader().forEachLine { line ->
            val parts = line.split("|")
            when (parts[0]) {
                "PID" -> scriptPid = parts.getOrNull(1)?.takeIf { it.all(Char::isDigit) }
                "FOUND" -> if (parts.size >= 5) found.put(JSONObject()
                    .put("name", parts[1]).put("path", parts[2])
                    .put("files", parts[3].toIntOrNull() ?: 0).put("kb", parts[4].toLongOrNull() ?: 0))
                "TOTAL" -> filesTotal = parts.getOrNull(1)?.toIntOrNull() ?: 0
                "FILE" -> filesDone = parts.getOrNull(1)?.toIntOrNull() ?: filesDone
                "WARN" -> warnings.put(parts.drop(1).joinToString("|"))
                "CHECKSUM" -> update("checksumming", "Computing checksums…", force = true)
                "NOSHA" -> warnings.put("sha256sum is not available on this phone")
                "DONE" -> done = true
                else -> { tail.addLast(line); if (tail.size > 5) tail.removeFirst() }
            }
            if (parts[0] == "FILE") update("copying", "Copied $filesDone of $filesTotal files")
        }
        val code = p.waitFor()
        when {
            cancelled -> {
                runSu("rm -rf $STAGING")
                update("cancelled", "Cancelled; the temporary copy was removed.", force = true)
            }
            done && code == 0 -> update("done", "Copied $filesDone files. You can return to your Mac.", force = true)
            else -> update("error", "The copy script stopped (exit code $code). ${tail.joinToString(" ")}".trim(),
                force = true)
        }
    }

    private fun runSu(command: String): Int = try {
        ProcessBuilder("su", "-c", command).redirectErrorStream(true).start().let {
            it.inputStream.readBytes()
            it.waitFor()
        }
    } catch (e: IOException) {
        -1
    }

    private fun update(state: String, message: String, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - lastWrite < 300) return
        lastWrite = now
        Status.write(this, JSONObject()
            .put("run_id", runId)
            .put("state", state)
            .put("message", message)
            .put("files_done", filesDone)
            .put("files_total", filesTotal)
            .put("found", found)
            .put("warnings", warnings))
    }
}

/** status.json in the app's external files dir; `adb shell cat` can read it without root. */
object Status {
    private const val TAG = "VTStatus"

    private fun file(ctx: Context) = File(ctx.getExternalFilesDir(null), "status.json")

    @Synchronized
    fun write(ctx: Context, status: JSONObject) {
        status.put("updated_at", System.currentTimeMillis())
        val text = status.toString()
        try {
            val f = file(ctx)
            val tmp = File(f.parentFile, "status.json.tmp")
            tmp.writeText(text)
            tmp.renameTo(f)
        } catch (e: Exception) {
            Log.w(TAG, "could not write status file: $e")
        }
        Log.i(TAG, text) // fallback channel: the Mac also reads `logcat -s VTStatus`
    }

    fun read(ctx: Context): JSONObject? = try {
        JSONObject(file(ctx).readText())
    } catch (e: Exception) {
        null
    }
}
