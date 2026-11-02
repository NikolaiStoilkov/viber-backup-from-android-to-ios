package com.vibertransfer.helper

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject

/**
 * The helper's only screen. The Mac opens it with
 *   am start -n com.vibertransfer.helper/.MainActivity -a com.vibertransfer.helper.PREPARE --es run_id <id>
 * and the user taps "Start copy" (so nothing is copied without a tap on the phone).
 * CANCEL and CLEANUP intents from the Mac are forwarded to [CopyService].
 */
class MainActivity : Activity() {
    companion object {
        const val ACTION_PREPARE = "com.vibertransfer.helper.PREPARE"
        const val ACTION_CANCEL = "com.vibertransfer.helper.CANCEL"
        const val ACTION_CLEANUP = "com.vibertransfer.helper.CLEANUP"
    }

    private lateinit var statusText: TextView
    private lateinit var progress: ProgressBar
    private lateinit var startButton: Button
    private var runId: String? = null
    private val handler = Handler(Looper.getMainLooper())
    private val refresher = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
        }
        layout.addView(TextView(this).apply { text = "Viber Transfer Helper"; textSize = 24f })
        layout.addView(TextView(this).apply {
            text = "Copies your Viber chats and media so the Viber Transfer app on your Mac can back them up. " +
                "It only reads Viber's data and never changes or deletes it. It needs root access."
            setPadding(0, pad / 2, 0, pad)
        })
        statusText = TextView(this).apply { textSize = 17f; setPadding(0, 0, 0, pad / 2) }
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 1000 }
        startButton = Button(this).apply {
            text = "Start copy"
            isEnabled = false
            setOnClickListener { startCopy() }
        }
        layout.addView(statusText)
        layout.addView(progress)
        layout.addView(startButton)
        setContentView(ScrollView(this).apply { addView(layout) })

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        if (savedInstanceState == null) handleIntent(intent)
        else runId = savedInstanceState.getString("run_id")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("run_id", runId)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresher)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refresher)
    }

    private fun handleIntent(intent: Intent?) {
        when (intent?.action) {
            ACTION_PREPARE -> {
                val id = intent.getStringExtra(CopyService.EXTRA_RUN_ID) ?: return
                if (CopyService.isRunning) return
                runId = id
                Status.write(this, JSONObject()
                    .put("run_id", id)
                    .put("state", "waiting_for_user")
                    .put("message", "Waiting for you to tap Start copy"))
            }
            ACTION_CANCEL -> startService(Intent(this, CopyService::class.java).setAction(CopyService.ACTION_CANCEL))
            ACTION_CLEANUP -> startService(Intent(this, CopyService::class.java).setAction(CopyService.ACTION_CLEANUP))
        }
    }

    private fun startCopy() {
        val id = runId ?: return
        startButton.isEnabled = false
        startForegroundService(Intent(this, CopyService::class.java)
            .setAction(CopyService.ACTION_START)
            .putExtra(CopyService.EXTRA_RUN_ID, id))
    }

    private fun refresh() {
        val s = Status.read(this)
        if (s == null || runId == null || s.optString("run_id") != runId) {
            statusText.text = "Start the transfer from the Viber Transfer app on your Mac."
            startButton.isEnabled = false
            progress.progress = 0
            return
        }
        val state = s.optString("state")
        statusText.text = when (state) {
            "waiting_for_user" -> "Your Mac is ready. Tap Start copy, then tap Grant when asked for root access."
            "requesting_root" -> "Tap Grant in the root permission prompt."
            else -> s.optString("message")
        }
        startButton.isEnabled = state == "waiting_for_user" && !CopyService.isRunning
        val total = s.optInt("files_total")
        progress.progress = if (total > 0) (1000L * s.optInt("files_done") / total).toInt() else 0
    }
}
