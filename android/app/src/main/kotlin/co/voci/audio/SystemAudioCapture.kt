package co.voci.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import kotlin.concurrent.thread

/**
 * Captures system playback audio (YouTube, Spotify, etc. — apps that don't opt out)
 * via [AudioPlaybackCaptureConfiguration]. Mirrors what `parec` does on Linux.
 *
 * Outputs 16 kHz mono 16-bit PCM frames in ~25 ms chunks (400 samples * 2 bytes = 800 bytes)
 * to match the Deepgram desktop config.
 */
class SystemAudioCapture(
    private val projection: MediaProjection,
    private val onPcmFrame: (ByteArray) -> Unit,
) {
    @Volatile private var running = false
    private var recordThread: Thread? = null

    fun start() {
        if (running) return
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()

        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()

        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = maxOf(minBuf, FRAME_BYTES * 8)

        val record = AudioRecord.Builder()
            .setAudioFormat(format)
            .setAudioPlaybackCaptureConfig(config)
            .setBufferSizeInBytes(bufferSize)
            .build()

        record.startRecording()
        running = true
        recordThread = thread(name = "audio-capture", isDaemon = true) {
            val frame = ByteArray(FRAME_BYTES)
            try {
                while (running) {
                    val n = record.read(frame, 0, FRAME_BYTES)
                    if (n <= 0) continue
                    if (n == FRAME_BYTES) {
                        onPcmFrame(frame.copyOf())
                    } else {
                        onPcmFrame(frame.copyOf(n))
                    }
                }
            } finally {
                try { record.stop() } catch (_: Exception) {}
                record.release()
            }
        }
    }

    fun stop() {
        running = false
        recordThread?.join(1000)
        recordThread = null
    }

    companion object {
        const val SAMPLE_RATE = 16000
        // 25 ms frame at 16 kHz mono 16-bit = 400 samples * 2 bytes
        const val FRAME_BYTES = 800
    }
}
