"""PSD -> Android APK packaging.

`psd apk app.psd` generates a complete, ready-to-build Android project
whose WebView app *runs your compiled PSD program on the phone*:

1. Your PSD web app is cross-compiled to a fully static, position-
   independent ARM64/x86_64 executable (llvmlite targets + `zig cc`
   musl static-pie — no Android NDK needed). A static PIE has no
   interpreter and no bionic dependency, so Android's kernel can run
   it straight from the app's nativeLibraryDir.
2. The executable ships as jniLibs/<abi>/libpsdapp.so with legacy
   packaging, so Android extracts it to nativeLibraryDir (the one
   app-writable location executables may run from since Android 10).
3. MainActivity starts it with ProcessBuilder (PSD_PORT env), waits
   for 127.0.0.1:<port> to answer, and loads it in a WebView.

APK *assembly* (aapt2/dex/signing) is done by Gradle + the Android
SDK — `psd apk --build` runs it when Java, Gradle and the SDK are
installed; otherwise the generated project opens in Android Studio
and builds there. That split is honest: APK signing/tooling is
Google's toolchain in every language (Flutter/React Native included),
and iOS IPA additionally requires a Mac with Xcode by Apple's rules.

Toolchain pieces PSD can use automatically:
  - zig: `pip install ziglang` (or a `zig` binary) for cross-compiling
  - NDK clang also works if ANDROID_NDK_HOME is set (preferred by SDK users)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# Android ABI -> (LLVM triple, zig target)
ABIS = {
    "arm64-v8a": ("aarch64-linux-musl", "aarch64-linux-musl"),
    "x86_64": ("x86_64-linux-musl", "x86_64-linux-musl"),
}

RUNTIME_C = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime.c")


def find_zig():
    """Return a command prefix for zig cc, or None."""
    exe = shutil.which("zig")
    if exe:
        return [exe, "cc"]
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        return None


def find_ndk_clang(abi: str):
    home = os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT")
    if not home:
        return None
    target = {"arm64-v8a": "aarch64-linux-android24-clang",
              "x86_64": "x86_64-linux-android24-clang"}.get(abi)
    for prebuilt in ("linux-x86_64", "darwin-x86_64", "darwin-arm64", "windows-x86_64"):
        cand = os.path.join(home, "toolchains", "llvm", "prebuilt", prebuilt, "bin", target)
        if os.path.exists(cand):
            return cand
    return None


def cross_build_android(psd_path: str, abi: str, out_path: str) -> tuple[bool, str]:
    """Cross-compile a PSD program to a static-PIE Android executable.

    Returns (ok, message). Prefers zig cc (musl static, no NDK);
    falls back to an NDK clang (bionic dynamic PIE, the classic route).
    """
    from .cli import frontend
    from .codegen_llvm import generate
    import llvmlite.binding as llvm

    llvm_triple, zig_target = ABIS[abi]
    llvm.initialize_all_targets()
    llvm.initialize_all_asmprinters()
    prog = frontend(psd_path)
    module = prog.module if hasattr(prog, "module") else prog
    ir_text = generate(module, triple=llvm_triple)
    mod = llvm.parse_assembly(ir_text)
    mod.verify()
    tm = llvm.Target.from_triple(llvm_triple).create_target_machine(opt=3, reloc="pic")
    obj = tm.emit_object(mod)

    zig = find_zig()
    ndk_cc = None if zig else find_ndk_clang(abi)
    if not zig and not ndk_cc:
        return False, ("no cross-compiler found: install zig "
                       "(`pip install ziglang`) or set ANDROID_NDK_HOME")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="psd-apk-") as td:
        obj_path = os.path.join(td, "prog.o")
        rt_obj = os.path.join(td, "runtime.o")
        with open(obj_path, "wb") as f:
            f.write(obj)
        if zig:
            cc = zig + ["-target", zig_target]
            link_extra = ["-fPIE", "-static-pie"]
        else:
            cc = [ndk_cc]
            link_extra = ["-fPIE", "-pie"]
        r = subprocess.run(cc + ["-O2", "-std=c11", "-fPIC", "-c", RUNTIME_C, "-o", rt_obj],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False, "runtime cross-compile failed:\n" + r.stderr[-1500:]
        r = subprocess.run(cc + link_extra + [obj_path, rt_obj, "-o", out_path, "-lm"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False, "android link failed:\n" + r.stderr[-1500:]
    return True, f"{abi}: {'zig musl static-pie' if zig else 'NDK clang PIE'}"


def detect_toolchain() -> dict:
    sdk = (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
           or os.path.expanduser("~/Android/Sdk"))
    return {
        "java": shutil.which("java"),
        "gradle": shutil.which("gradle"),
        "sdk": sdk if os.path.isdir(sdk) else None,
        "zig": find_zig(),
        "ndk": os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT"),
    }


MAIN_ACTIVITY = """package {package};

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.Socket;

public class MainActivity extends Activity {{
    private Process psdProcess;
    private WebView webView;
    private static final int PORT = {port};

    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        setContentView(webView);
        new Thread(this::startPsdServer).start();
    }}

    private void startPsdServer() {{
        String error = null;
        try {{
            File bin = new File(getApplicationInfo().nativeLibraryDir, "libpsdapp.so");
            if (!bin.exists()) {{
                error = "PSD binary missing for this device CPU (libpsdapp.so).";
            }} else {{
                ProcessBuilder pb = new ProcessBuilder(bin.getAbsolutePath());
                pb.environment().put("PSD_PORT", String.valueOf(PORT));
                pb.directory(getFilesDir());
                pb.redirectErrorStream(true);
                pb.redirectOutput(new File(getFilesDir(), "psd.log"));
                psdProcess = pb.start();
                if (!waitForPort(PORT, 100)) {{
                    error = "PSD server did not start; see psd.log in app files.";
                }}
            }}
        }} catch (Exception e) {{
            error = "Could not start PSD server: " + e.getMessage();
        }}
        final String err = error;
        runOnUiThread(() -> {{
            if (err == null) {{
                webView.loadUrl("http://127.0.0.1:" + PORT + "/");
            }} else {{
                webView.loadData("<html><body style='font-family:sans-serif;padding:24px'>"
                        + "<h2>{name}</h2><p>" + err + "</p></body></html>",
                        "text/html", "utf-8");
            }}
        }});
    }}

    private boolean waitForPort(int port, int tries) {{
        for (int i = 0; i < tries; i++) {{
            try (Socket s = new Socket("127.0.0.1", port)) {{
                return true;
            }} catch (Exception ignored) {{
                try {{ Thread.sleep(100); }} catch (InterruptedException e) {{ return false; }}
            }}
        }}
        return false;
    }}

    @Override
    protected void onDestroy() {{
        if (psdProcess != null) {{
            psdProcess.destroy();
        }}
        super.onDestroy();
    }}
}}
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

    <application
        android:label="{name}"
        android:extractNativeLibs="true"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.DeviceDefault.NoActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

APP_BUILD_GRADLE = """plugins {
    id 'com.android.application'
}

android {
    namespace '__PACKAGE__'
    compileSdk 34

    defaultConfig {
        applicationId '__PACKAGE__'
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName '1.0'
        ndk {
            abiFilters __ABI_FILTERS__
        }
    }

    // The PSD program is a standalone executable packaged as a .so;
    // legacy packaging extracts it to nativeLibraryDir so it can run.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}
"""

ROOT_BUILD_GRADLE = """buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath 'com.android.tools.build:gradle:8.5.2'
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}
"""

PROJECT_README = """# {name} — Android app powered by PSD

Generated by `psd apk`. Your PSD program (`{psd_file}`) is compiled to a
native Android executable (`app/src/main/jniLibs/<abi>/libpsdapp.so`) and
runs on the phone itself; MainActivity starts it and shows its local
web UI (`http://127.0.0.1:{port}`) in a WebView. No server, no network
needed for the app to work.

## Build the APK

Option A — Android Studio: open this folder, let it sync, then
Build > Build APK(s). Output: `app/build/outputs/apk/debug/app-debug.apk`.

Option B — command line (needs JDK 17+, Android SDK platform 34 +
build-tools, Gradle 8+):

    gradle assembleDebug        # or: psd apk {psd_file} --build (from PSD)

Install on a device/emulator: `adb install app-debug.apk`, or copy the
APK to the phone. For the Play Store, build a signed release AAB with
your own keystore (Build > Generate Signed Bundle in Android Studio).

## Notes

- The PSD app must listen on port {port} (MainActivity sets PSD_PORT).
  `examples/mobile_app.psd` already follows this pattern.
- ABIs included: {abis}. Real phones are arm64-v8a; x86_64 is for emulators.
- App data/permissions: the manifest requests INTERNET (needed even for
  loopback sockets on some devices). Everything runs locally.
- iOS: IPA builds require a Mac with Xcode (Apple's rule for every
  toolchain — Flutter and React Native included), so `psd apk` targets
  Android; the same PSD web UI is the basis for a future iOS shell.
"""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def generate_project(psd_path: str, out_dir: str, name: str, package: str,
                     port: int, abis: list[str]) -> dict:
    """Generate the Android project; cross-compile binaries for `abis`.

    Returns {"binaries": {abi: message}, "toolchain": {...}}.
    """
    result = {"binaries": {}, "toolchain": detect_toolchain()}
    pkg_path = os.path.join(out_dir, "app", "src", "main", "java", *package.split("."))

    _write(os.path.join(out_dir, "settings.gradle"),
           f"rootProject.name = '{name}'\ninclude ':app'\n")
    _write(os.path.join(out_dir, "build.gradle"), ROOT_BUILD_GRADLE)
    _write(os.path.join(out_dir, "gradle.properties"),
           "android.useAndroidX=false\norg.gradle.jvmargs=-Xmx2g\n")
    abi_filters = ", ".join(f"'{a}'" for a in abis)
    _write(os.path.join(out_dir, "app", "build.gradle"),
           APP_BUILD_GRADLE.replace("__PACKAGE__", package)
                            .replace("__ABI_FILTERS__", abi_filters))
    _write(os.path.join(out_dir, "app", "src", "main", "AndroidManifest.xml"),
           MANIFEST.format(name=name))
    _write(os.path.join(pkg_path, "MainActivity.java"),
           MAIN_ACTIVITY.format(package=package, port=port, name=name))
    _write(os.path.join(out_dir, "app", "src", "main", "res", "values", "strings.xml"),
           f'<?xml version="1.0" encoding="utf-8"?>\n'
           f'<resources><string name="app_name">{name}</string></resources>\n')
    _write(os.path.join(out_dir, ".gitignore"),
           "build/\n.gradle/\nlocal.properties\n*.iml\napp/build/\n")

    for abi in abis:
        out = os.path.join(out_dir, "app", "src", "main", "jniLibs", abi, "libpsdapp.so")
        ok, msg = cross_build_android(psd_path, abi, out)
        result["binaries"][abi] = msg if ok else f"SKIPPED ({msg})"

    _write(os.path.join(out_dir, "README.md"),
           PROJECT_README.format(name=name, psd_file=os.path.basename(psd_path),
                                 port=port, abis=", ".join(abis)))
    return result


def build_with_gradle(out_dir: str) -> tuple[bool, str]:
    """Run `gradle assembleDebug` if the full toolchain exists."""
    tc = detect_toolchain()
    missing = [k for k in ("java", "gradle", "sdk") if not tc.get(k)]
    if missing:
        return False, ("cannot assemble APK here — missing: " + ", ".join(missing)
                       + ". Open the project in Android Studio, or install "
                         "JDK 17 + Android SDK + Gradle and retry.")
    with open(os.path.join(out_dir, "local.properties"), "w") as f:
        f.write(f"sdk.dir={tc['sdk']}\n")
    r = subprocess.run([tc["gradle"], "assembleDebug"], cwd=out_dir,
                       capture_output=True, text=True, timeout=1200)
    apk = os.path.join(out_dir, "app", "build", "outputs", "apk", "debug",
                       "app-debug.apk")
    if r.returncode == 0 and os.path.exists(apk):
        return True, apk
    return False, "gradle failed:\n" + (r.stderr or r.stdout)[-1500:]
