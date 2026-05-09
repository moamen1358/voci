package co.voci.stt

import android.util.Log
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/**
 * Deepgram Listen API streaming client — direct WebSocket via OkHttp.
 * Mirrors the Python `voci/deepgram_stt.py` settings 1:1.
 */
class DeepgramStreamingClient(
    private val apiKey: String,
    private val sampleRate: Int,
    private val language: String = "en",
    private val model: String = "nova-2",
    private val endpointingMs: Int = 25,
    private val onPartial: (String) -> Unit,
    private val onCommitted: (String) -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var ws: WebSocket? = null
    @Volatile private var open = false
    private val json = Json { ignoreUnknownKeys = true }

    fun start() {
        val url = buildString {
            append("wss://api.deepgram.com/v1/listen")
            append("?model=").append(model)
            append("&language=").append(language)
            append("&encoding=linear16")
            append("&sample_rate=").append(sampleRate)
            append("&channels=1")
            append("&interim_results=true")
            append("&smart_format=false")
            append("&punctuate=true")
            append("&endpointing=").append(endpointingMs)
            append("&utterance_end_ms=1000")
            append("&vad_events=true")
            append("&no_delay=true")
        }
        val req = Request.Builder()
            .url(url)
            .addHeader("Authorization", "Token $apiKey")
            .build()
        ws = client.newWebSocket(req, listener)
    }

    fun sendPcm(pcm: ByteArray) {
        if (!open) return
        ws?.send(pcm.toByteString())
    }

    fun stop() {
        open = false
        ws?.close(1000, "client stop")
        ws = null
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "Deepgram socket open")
            open = true
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val root = json.parseToJsonElement(text).jsonObject
                val type = root["type"]?.jsonPrimitive?.contentOrNull
                if (type != "Results") return
                val alt = root["channel"]?.jsonObject
                    ?.get("alternatives")?.jsonArray?.firstOrNull()?.jsonObject
                val transcript = alt?.get("transcript")?.jsonPrimitive?.contentOrNull.orEmpty()
                if (transcript.isBlank()) return
                val isFinal = root["is_final"]?.jsonPrimitive?.boolean ?: false
                if (isFinal) onCommitted(transcript) else onPartial(transcript)
            } catch (e: Exception) {
                Log.w(TAG, "parse failed: ${e.message}")
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            open = false
            Log.i(TAG, "Deepgram closing: $code $reason")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            open = false
            Log.e(TAG, "Deepgram failure: ${t.message}")
        }
    }

    companion object { private const val TAG = "DeepgramStream" }
}
