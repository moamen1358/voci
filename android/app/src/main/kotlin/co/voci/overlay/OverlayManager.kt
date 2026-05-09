package co.voci.overlay

import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import co.voci.R

/**
 * Two-line floating subtitle window via SYSTEM_ALERT_WINDOW.
 * Click-through (touches pass to the app underneath). Mirrors the desktop overlay.
 */
class OverlayManager(private val context: Context) {

    private val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val main = Handler(Looper.getMainLooper())
    private var view: View? = null
    private var topLabel: TextView? = null
    private var bottomLabel: TextView? = null

    fun show() = main.post {
        if (view != null) return@post
        val v = LayoutInflater.from(context).inflate(R.layout.overlay_lines, null, false)
        topLabel = v.findViewById(R.id.top_text)
        bottomLabel = v.findViewById(R.id.bottom_text)

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE

        val flags = (WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
            or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
            or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
            or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN)

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            flags,
            PixelFormat.TRANSLUCENT,
        )
        params.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
        params.y = 96  // px from bottom

        wm.addView(v, params)
        view = v
    }

    fun setTopText(text: String) = main.post { topLabel?.text = text }

    fun setBottomText(text: String) = main.post { bottomLabel?.text = text }

    fun hide() = main.post {
        view?.let { wm.removeView(it) }
        view = null
        topLabel = null
        bottomLabel = null
    }
}
