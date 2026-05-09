package co.voci

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import co.voci.audio.SystemAudioCapture
import co.voci.overlay.OverlayManager
import co.voci.stt.DeepgramStreamingClient
import co.voci.translate.MyMemoryClient
import co.voci.util.PaginatedLine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Foreground service that ties together:
 *   - SystemAudioCapture (AudioPlaybackCaptureConfiguration)
 *   - DeepgramStreamingClient (WebSocket STT)
 *   - MyMemoryClient (HTTP translation)
 *   - OverlayManager (SYSTEM_ALERT_WINDOW two-line subtitle box)
 */
class VociService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var translateJob: Job? = null

    private var projection: MediaProjection? = null
    private var capture: SystemAudioCapture? = null
    private var stt: DeepgramStreamingClient? = null
    private val mymemory = MyMemoryClient()
    private lateinit var overlay: OverlayManager

    private val englishLine = PaginatedLine(wordsPerPage = 10)
    private val arabicLine = PaginatedLine(wordsPerPage = 10)

    override fun onCreate() {
        super.onCreate()
        overlay = OverlayManager(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutdown()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, buildNotification())

        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, 0) ?: 0
        val resultData = intent?.getParcelableExtra<Intent>(EXTRA_RESULT_DATA)
        if (resultCode == 0 || resultData == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = mpm.getMediaProjection(resultCode, resultData)
        if (projection == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        val prefs = getSharedPreferences("voci", MODE_PRIVATE)
        val apiKey = prefs.getString(MainActivity.KEY_DEEPGRAM, "") ?: ""
        val targetLang = prefs.getString(MainActivity.KEY_TARGET_LANG, "ar") ?: "ar"

        overlay.show()

        // STT
        stt = DeepgramStreamingClient(
            apiKey = apiKey,
            sampleRate = SystemAudioCapture.SAMPLE_RATE,
            onPartial = { partial -> overlay.setTopText(englishLine.withPartial(partial)) },
            onCommitted = { committed ->
                englishLine.commit(committed)
                overlay.setTopText(englishLine.committedDisplay())
                if (targetLang != "en") translate(committed, targetLang)
            },
        ).also { it.start() }

        // Audio capture → STT
        capture = SystemAudioCapture(
            projection = projection!!,
            onPcmFrame = { pcm -> stt?.sendPcm(pcm) },
        ).also { it.start() }

        return START_STICKY
    }

    private fun translate(text: String, target: String) {
        translateJob?.cancel()
        translateJob = scope.launch {
            val translated = mymemory.translate(text, srcLang = "en", targetLang = target)
            arabicLine.commit(translated)
            overlay.setBottomText(arabicLine.committedDisplay())
        }
    }

    private fun shutdown() {
        capture?.stop(); capture = null
        stt?.stop(); stt = null
        projection?.stop(); projection = null
        scope.cancel()
        overlay.hide()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (capture != null || stt != null) shutdown()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, "voci", NotificationManager.IMPORTANCE_LOW)
                )
            }
        }
        val stopIntent = PendingIntent.getService(
            this, 0,
            Intent(this, VociService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("voci")
            .setContentText("Streaming subtitles…")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopIntent)
            .setOngoing(true)
            .build()
    }

    companion object {
        const val CHANNEL_ID = "voci_running"
        const val NOTIF_ID = 1
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val ACTION_STOP = "co.voci.STOP"

        fun start(ctx: Context, resultCode: Int, resultData: Intent) {
            val intent = Intent(ctx, VociService::class.java)
                .putExtra(EXTRA_RESULT_CODE, resultCode)
                .putExtra(EXTRA_RESULT_DATA, resultData)
            ctx.startForegroundService(intent)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, VociService::class.java).setAction(ACTION_STOP))
        }
    }
}
