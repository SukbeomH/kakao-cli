// SPDX-License-Identifier: MIT
package org.androidconsole.ime;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.inputmethodservice.InputMethodService;
import android.util.Base64;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import org.json.JSONObject;

/** Shell-only Unicode/preedit input. No network, clipboard, storage or editor reads. */
public final class ConsoleIME extends InputMethodService {
    private static final String ACTION = "org.androidconsole.ime.COMPOSITION";
    private String active;
    private InputConnection connection;
    private final LinkedHashSet<String> retired = new LinkedHashSet<>();
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            try {
                InputConnection ic = getCurrentInputConnection();
                if (ic == null) { setResultCode(409); return; }
                String action = intent.getAction();
                if (ACTION.equals(action)) {
                    JSONObject body = new JSONObject(new String(Base64.decode(intent.getStringExtra("payload"), Base64.DEFAULT), StandardCharsets.UTF_8));
                    String id = body.getString("session"), phase = body.getString("phase");
                    String text = body.optString("text", "");
                    if (id.length() < 1 || id.length() > 128 || text.length() > 32768) { setResultCode(400); return; }
                    if ("start".equals(phase)) {
                        // Never steal another viewer's composition or revive an old field.
                        if (retired.contains(id)) { setResultCode(409); return; }
                        if (active != null) {
                            if (!active.substring(0, active.lastIndexOf(':')).equals(id.substring(0, id.lastIndexOf(':')))) { setResultCode(409); return; }
                            // A new explicit composition from the same viewer recovers
                            // after an uncertain ACK; it never replays the old input.
                            ic.finishComposingText(); retire();
                        }
                        active = id; connection = ic;
                    } else {
                        if (!id.equals(active) || connection != ic) { setResultCode(409); return; }
                        boolean ok;
                        if ("update".equals(phase)) ok = ic.setComposingText(text, 1);
                        else if ("commit".equals(phase)) { ok = ic.commitText(text, 1); retire(); }
                        else { setResultCode(400); return; }
                        if (!ok) { retire(); setResultCode(503); return; }
                    }
                } else if ("ADB_INPUT_TEXT".equals(action)) {
                    // Compatibility with CLI/TUI after this IME has been selected.
                    retire();
                    if (!ic.commitText(intent.getStringExtra("msg"), 1)) { setResultCode(503); return; }
                } else if ("ADB_CLEAR_TEXT".equals(action)) {
                    retire(); ic.finishComposingText();
                    ic.performContextMenuAction(android.R.id.selectAll); ic.commitText("", 1);
                } else { setResultCode(400); return; }
                setResultCode(200);
                setResultData("android-console-ime/1");
            } catch (Exception ignored) { setResultCode(400); }
        }
    };
    private void retire() {
        if (active != null) {
            retired.add(active);
            if (retired.size() > 128) retired.remove(retired.iterator().next());
        }
        active = null; connection = null;
    }
    @Override public void onCreate() {
        super.onCreate();
        IntentFilter filter = new IntentFilter(ACTION);
        filter.addAction("ADB_INPUT_TEXT"); filter.addAction("ADB_CLEAR_TEXT");
        // Android's shell/root holds DUMP; ordinary installed apps cannot inject text.
        registerReceiver(receiver, filter, "android.permission.DUMP", null);
    }
    @Override public boolean onEvaluateInputViewShown() { return false; }
    @Override public void onStartInput(EditorInfo info, boolean restarting) {
        retire(); super.onStartInput(info, restarting);
    }
    @Override public void onFinishInput() { retire(); super.onFinishInput(); }
    @Override public void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
}
