package co.voci.translate

import android.util.Log
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit

/**
 * MyMemory free translation API. No key required (5K chars/day anonymous).
 * Mirrors `voci/mymemory_translate.py`.
 */
class MyMemoryClient(
    private val email: String? = null,
) {
    private val client = OkHttpClient.Builder()
        .callTimeout(6, TimeUnit.SECONDS)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    fun translate(text: String, srcLang: String = "en", targetLang: String = "ar"): String {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return ""
        if (srcLang == targetLang) return trimmed
        val q = URLEncoder.encode(trimmed, StandardCharsets.UTF_8)
        val pair = URLEncoder.encode("$srcLang|$targetLang", StandardCharsets.UTF_8)
        val sb = StringBuilder("https://api.mymemory.translated.net/get?q=").append(q).append("&langpair=").append(pair)
        email?.takeIf { it.isNotBlank() }?.let { sb.append("&de=").append(URLEncoder.encode(it, StandardCharsets.UTF_8)) }
        return try {
            val req = Request.Builder().url(sb.toString()).build()
            client.newCall(req).execute().use { resp ->
                val body = resp.body?.string().orEmpty()
                val obj = json.parseToJsonElement(body).jsonObject
                val statusOk = obj["responseStatus"]?.jsonPrimitive?.contentOrNull?.let { it == "200" } ?: false
                if (!statusOk) {
                    Log.w(TAG, "MyMemory error: ${obj["responseDetails"]?.jsonPrimitive?.contentOrNull}")
                    return trimmed
                }
                obj["responseData"]?.jsonObject?.get("translatedText")?.jsonPrimitive?.contentOrNull.orEmpty()
            }
        } catch (e: Exception) {
            Log.w(TAG, "translate failed: ${e.message}")
            trimmed
        }
    }

    companion object { private const val TAG = "MyMemory" }
}
