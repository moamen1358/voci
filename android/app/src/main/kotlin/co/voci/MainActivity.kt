package co.voci

import android.app.Activity
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import co.voci.databinding.ActivityMainBinding

/**
 * Entry screen: collects Deepgram API key, requests SYSTEM_ALERT_WINDOW + POST_NOTIFICATIONS,
 * fires the MediaProjection consent dialog, then hands the projection token to VociService.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val prefs by lazy { getSharedPreferences("voci", MODE_PRIVATE) }

    private val notificationPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* result irrelevant; we just need it asked once */ }

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            // Persist the API key now that we're committing to launch
            prefs.edit()
                .putString(KEY_DEEPGRAM, binding.deepgramKey.text.toString().trim())
                .putString(KEY_TARGET_LANG, binding.targetLang.text.toString().trim().ifEmpty { "ar" })
                .apply()
            VociService.start(this, result.resultCode, result.data!!)
            binding.statusText.text = getString(R.string.status_running)
        } else {
            binding.statusText.text = getString(R.string.status_consent_denied)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.deepgramKey.setText(prefs.getString(KEY_DEEPGRAM, "") ?: "")
        binding.targetLang.setText(prefs.getString(KEY_TARGET_LANG, "ar") ?: "ar")

        binding.startButton.setOnClickListener {
            val key = binding.deepgramKey.text.toString().trim()
            if (key.isEmpty()) {
                binding.statusText.text = getString(R.string.status_need_key)
                return@setOnClickListener
            }
            ensureOverlayPermission()
            ensureNotificationPermission()
            requestProjection()
        }

        binding.stopButton.setOnClickListener {
            VociService.stop(this)
            binding.statusText.text = getString(R.string.status_stopped)
        }
    }

    private fun ensureOverlayPermission() {
        if (!Settings.canDrawOverlays(this)) {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            )
            startActivity(intent)
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun requestProjection() {
        val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projectionLauncher.launch(mpm.createScreenCaptureIntent())
    }

    companion object {
        const val KEY_DEEPGRAM = "deepgram_key"
        const val KEY_TARGET_LANG = "target_lang"
    }
}
