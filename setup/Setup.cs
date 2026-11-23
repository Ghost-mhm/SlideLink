// SlideLink-Setup.exe: Windows installer with a window or silent (/S); the app files travel inside the EXE.
// SlideLink-Setup.exe: installs SlideLink for the current user (no admin rights needed).
// The app files travel inside this EXE (resource "payload.zip"); install.ps1 from it does the actual install.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("SlideLink Setup")]
[assembly: AssemblyProduct("SlideLink")]
[assembly: AssemblyVersion("1.4.1.0")]
[assembly: AssemblyFileVersion("1.4.1.0")]

// Entry point: window, silent install (/S) or a picture of the window (/Snapshot).
static class Program
{
    public const string Version = "1.4.1";

    [STAThread]
    // Starts the setup in the chosen mode.
    static int Main(string[] args)
    {
        // SlideLink-Setup.exe /S  = silent install (no window), e.g. for updates or testing.
        // Optional: /NoDesktop /Startup /Launch. Exit code 0 = installed. A log goes to %TEMP%\SlideLink-setup.log
        if (Array.Exists(args, a => a.Equals("/S", StringComparison.OrdinalIgnoreCase)))
        {
            Func<string, bool> has = f => Array.Exists(args, a => a.Equals(f, StringComparison.OrdinalIgnoreCase));
            string logPath = Path.Combine(Path.GetTempPath(), "SlideLink-setup.log");
            using (var w = new StreamWriter(logPath, false))
            {
                string err = SetupForm.Install(!has("/NoDesktop"), has("/Startup"), has("/Launch"), line => { w.WriteLine(line); w.Flush(); });
                w.WriteLine(err == null ? "OK" : "ERROR: " + err);
                return err == null ? 0 : 1;
            }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        // test proof: SlideLink-Setup.exe /Snapshot <file.png> saves a picture of the setup window and exits
        int snap = Array.FindIndex(args, a => a.Equals("/Snapshot", StringComparison.OrdinalIgnoreCase));
        if (snap >= 0 && snap + 1 < args.Length)
        {
            var f = new SetupForm { Opacity = 0, ShowInTaskbar = false };
            f.Show(); Application.DoEvents();
            using (var bmp = new Bitmap(f.Width, f.Height)) { f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height)); bmp.Save(args[snap + 1]); }
            f.Close();
            return 0;
        }
        Application.Run(new SetupForm());
        return 0;
    }
}

// The setup window: checks, options, progress and log.
class SetupForm : Form
{
    // Text in the Windows display language (German or English).
    public static string T(string en, string de)
    {
        return System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "de" ? de : en;
    }

    static readonly Color Bg = Color.FromArgb(20, 21, 28), Panel = Color.FromArgb(32, 34, 45),
        Accent = Color.FromArgb(124, 131, 255), Muted = Color.FromArgb(170, 174, 196), Good = Color.FromArgb(110, 214, 150),
        Bad = Color.FromArgb(255, 138, 138);

    readonly Label pyLabel = new Label(), browserLabel = new Label(), statusLabel = new Label();
    readonly CheckBox desktopBox = new CheckBox(), startupBox = new CheckBox(), launchBox = new CheckBox();
    readonly Button installBtn = new Button(), cancelBtn = new Button(), pythonBtn = new Button();
    readonly ProgressBar progress = new ProgressBar();
    readonly TextBox log = new TextBox();
    string python;          // python.exe to run SlideLink with
    bool done;
    readonly string dest = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "SlideLink");

    public SetupForm()
    {
        Text = "SlideLink Setup";
        ClientSize = new Size(580, 470);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Bg;
        ForeColor = Color.White;
        Font = new Font("Segoe UI", 10f);
        try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        var logo = new PictureBox { Location = new Point(28, 24), Size = new Size(56, 56), SizeMode = PictureBoxSizeMode.Zoom };
        try { logo.Image = Icon.ExtractAssociatedIcon(Application.ExecutablePath).ToBitmap(); } catch { }
        Controls.Add(logo);
        Controls.Add(new Label { Text = "SlideLink", Font = new Font("Segoe UI Semibold", 22f), AutoSize = true, Location = new Point(96, 20) });
        Controls.Add(new Label { Text = T("Presentations with phone remote and Claude · Version ", "Präsentationen mit Handy-Fernbedienung und Claude · Version ") + Program.Version, ForeColor = Muted,
            AutoSize = true, Location = new Point(99, 62) });

        bool update = Directory.Exists(dest);
        var box = new Panel { Location = new Point(28, 104), Size = new Size(524, 92), BackColor = Panel };
        Controls.Add(box);
        box.Controls.Add(new Label { Text = update ? T("Updates SlideLink. Your presentations and linked phones stay.", "Aktualisiert SlideLink. Deine Präsentationen und verknüpften Handys bleiben.")
                                                   : T("Installs SlideLink for you (no admin rights needed).", "Installiert SlideLink für dich (keine Admin-Rechte nötig)."),
            ForeColor = Muted, Location = new Point(14, 10), Size = new Size(500, 22) });
        pyLabel.Location = new Point(14, 36); pyLabel.Size = new Size(360, 22); box.Controls.Add(pyLabel);
        browserLabel.Location = new Point(14, 60); browserLabel.Size = new Size(500, 22); box.Controls.Add(browserLabel);
        Style(pythonBtn, false); pythonBtn.Text = T("Install Python", "Python installieren"); pythonBtn.Location = new Point(384, 32); pythonBtn.Size = new Size(126, 30);
        pythonBtn.Visible = false; pythonBtn.Click += InstallPython; box.Controls.Add(pythonBtn);

        int y = 212;
        var boxes = new[] { desktopBox, startupBox, launchBox };
        var texts = new[] { T("Desktop shortcut", "Verknüpfung auf dem Desktop"), T("Start with Windows (keeps your phone connected)", "Mit Windows starten (hält dein Handy verbunden)"), T("Start SlideLink when finished", "SlideLink danach starten") };
        var ons = new[] { true, false, true };
        for (int i = 0; i < boxes.Length; i++)
        {
            var cb = boxes[i];
            cb.Text = texts[i]; cb.Checked = ons[i]; cb.AutoSize = true; cb.Location = new Point(30, y); cb.ForeColor = Color.White;
            Controls.Add(cb); y += 30;
        }
        if (update) startupBox.Checked = File.Exists(StartupLink());

        progress.Location = new Point(28, 312); progress.Size = new Size(524, 8); progress.Style = ProgressBarStyle.Marquee;
        progress.MarqueeAnimationSpeed = 0; progress.Visible = false; Controls.Add(progress);
        log.Location = new Point(28, 326); log.Size = new Size(524, 74); log.Multiline = true; log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Vertical; log.BackColor = Panel; log.ForeColor = Muted; log.BorderStyle = BorderStyle.None;
        log.Font = new Font("Consolas", 8.5f); log.Visible = false; Controls.Add(log);
        statusLabel.Location = new Point(28, 316); statusLabel.Size = new Size(524, 60); statusLabel.ForeColor = Muted; Controls.Add(statusLabel);

        Style(installBtn, true); installBtn.Text = update ? T("Update", "Aktualisieren") : T("Install", "Installieren"); installBtn.Location = new Point(392, 414); installBtn.Size = new Size(160, 40);
        installBtn.Click += OnInstall; Controls.Add(installBtn);
        Style(cancelBtn, false); cancelBtn.Text = T("Cancel", "Abbrechen"); cancelBtn.Location = new Point(268, 414); cancelBtn.Size = new Size(112, 40);
        cancelBtn.Click += (s, e) => Close(); Controls.Add(cancelBtn);
        AcceptButton = installBtn;
        CheckRequirements();
    }

    // Shows a path with %USERPROFILE% instead of the user folder.
    static string Shorten(string p) { return p.Replace(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "%USERPROFILE%"); }
    // Path of the 'Start with Windows' shortcut.
    static string StartupLink() { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "SlideLink.lnk"); }

    // Button style (primary or secondary).
    void Style(Button b, bool primary)
    {
        b.FlatStyle = FlatStyle.Flat; b.FlatAppearance.BorderSize = 0; b.Cursor = Cursors.Hand;
        b.BackColor = primary ? Accent : Color.FromArgb(48, 51, 66); b.ForeColor = primary ? Bg : Color.White;
        b.Font = new Font("Segoe UI Semibold", 10.5f);
    }

    // ---------- requirements ----------
    // Checks Python and the browser and enables Install.
    void CheckRequirements()
    {
        python = FindPython();
        pyLabel.Text = python != null ? T("✓ Python found", "✓ Python gefunden") : T("✗ Python 3.10 or newer is needed", "✗ Python 3.10 oder neuer wird benötigt");
        pyLabel.ForeColor = python != null ? Good : Bad;
        pythonBtn.Visible = python == null;
        string browser = FindBrowser();
        browserLabel.Text = browser != null ? "✓ " + browser + T(" found (SlideLink's window)", " gefunden (SlideLink-Fenster)") : T("! No Chrome or Edge found: SlideLink opens in your default browser", "! Kein Chrome oder Edge gefunden: SlideLink öffnet sich im Standardbrowser");
        browserLabel.ForeColor = browser != null ? Good : Muted;
        installBtn.Enabled = python != null;
        statusLabel.Text = python == null ? T("Press \"Install Python\" (uses Windows' winget), then Install.", "Tippe auf \"Python installieren\" (nutzt winget von Windows), dann auf Installieren.") : "";
    }

    // Finds Python 3.10+ (registry, usual folders, py, python); ignores the Store alias.
    static string FindPython()
    {
        var candidates = new List<string>();
        foreach (var hive in new[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            using (var core = hive.OpenSubKey(@"Software\Python\PythonCore"))
            {
                if (core == null) continue;
                foreach (var ver in core.GetSubKeyNames())
                    using (var ip = core.OpenSubKey(ver + @"\InstallPath"))
                    {
                        if (ip == null) continue;
                        var exe = ip.GetValue("ExecutablePath") as string;
                        var dir = ip.GetValue("") as string;
                        if (string.IsNullOrEmpty(exe) && dir != null) exe = Path.Combine(dir, "python.exe");
                        if (!string.IsNullOrEmpty(exe)) candidates.Add(exe);
                    }
            }
        }
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        foreach (var root in new[] { Path.Combine(local, "Python"), Path.Combine(local, @"Programs\Python") })
            if (Directory.Exists(root))
                foreach (var d in Directory.GetDirectories(root)) candidates.Add(Path.Combine(d, "python.exe"));
        var fromPath = Run("py", "-3 -c \"import sys;print(sys.executable)\"");
        if (fromPath != null) candidates.Add(fromPath.Trim());
        fromPath = Run("python", "-c \"import sys;print(sys.executable)\"");
        if (fromPath != null) candidates.Add(fromPath.Trim());

        string best = null; Version bestV = null;
        foreach (var exe in candidates)
        {
            if (!File.Exists(exe) || exe.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            if (!File.Exists(Path.Combine(Path.GetDirectoryName(exe), "pythonw.exe"))) continue;
            var outp = Run(exe, "-c \"import sys;print('%d.%d' % sys.version_info[:2])\"");
            Version v;
            if (outp == null || !System.Version.TryParse(outp.Trim(), out v) || v < new Version(3, 10)) continue;
            if (bestV == null || v > bestV) { best = exe; bestV = v; }
        }
        return best;
    }

    // Finds Chrome or Edge.
    static string FindBrowser()
    {
        var exes = new[] { "chrome.exe", "msedge.exe" };
        var names = new[] { "Google Chrome", "Microsoft Edge" };
        for (int i = 0; i < exes.Length; i++)
            foreach (var hive in new[] { Registry.CurrentUser, Registry.LocalMachine })
                using (var k = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + exes[i]))
                {
                    var path = k == null ? null : k.GetValue("") as string;
                    if (path != null && File.Exists(path)) return names[i];
                }
        return null;
    }

    // Runs a program and returns its output (or null).
    static string Run(string exe, string args)
    {
        try
        {
            var psi = new ProcessStartInfo(exe, args) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            using (var p = Process.Start(psi))
            {
                string o = p.StandardOutput.ReadToEnd();
                if (!p.WaitForExit(15000)) { try { p.Kill(); } catch { } return null; }
                return p.ExitCode == 0 ? o : null;
            }
        }
        catch { return null; }
    }

    // Installs Python with winget; without winget it opens python.org and waits.
    void InstallPython(object s, EventArgs e)
    {
        var r = MessageBox.Show(T("SlideLink needs Python. Install Python 3.13 now with Windows' package manager (winget)?\n\n", "SlideLink braucht Python. Python 3.13 jetzt mit der Windows-Paketverwaltung (winget) installieren?\n\n") +
                                T("A window opens; follow it, then come back here.", "Ein Fenster öffnet sich; folge ihm und komm dann hierher zurück."), T("Install Python", "Python installieren"), MessageBoxButtons.OKCancel, MessageBoxIcon.Question);
        if (r != DialogResult.OK) return;
        pythonBtn.Enabled = false; statusLabel.Text = T("Installing Python…", "Python wird installiert…");
        new Thread(() =>
        {
            try
            {
                using (var p = Process.Start(new ProcessStartInfo("winget", "install -e --id Python.Python.3.13 --scope user") { UseShellExecute = true }))
                    p.WaitForExit();
            }
            catch (Exception)
            {
                // no winget on this PC (older Windows): open the official download page and wait for Python
                BeginInvoke((Action)(() =>
                {
                    MessageBox.Show(T("Windows' package manager (winget) isn't available on this PC.\n\n", "Die Windows-Paketverwaltung (winget) ist auf diesem PC nicht verfügbar.\n\n") +
                        T("The Python download page opens now. Install Python (tick \"Add python.exe to PATH\"), then come back: this setup notices it by itself.", "Die Python-Downloadseite öffnet sich jetzt. Installiere Python (\"Add python.exe to PATH\" ankreuzen) und komm dann zurück: Das Setup bemerkt es von selbst."), T("Install Python", "Python installieren"), MessageBoxButtons.OK, MessageBoxIcon.Information);
                    try { Process.Start(new ProcessStartInfo("https://www.python.org/downloads/windows/") { UseShellExecute = true }); } catch { }
                    statusLabel.Text = T("Waiting for Python to be installed…", "Warte auf die Python-Installation…");
                }));
                for (int i = 0; i < 400 && FindPython() == null; i++) Thread.Sleep(3000);   // check every 3 s, up to 20 min
            }
            BeginInvoke((Action)(() => { pythonBtn.Enabled = true; CheckRequirements(); }));
        }) { IsBackground = true }.Start();
    }

    // ---------- install ----------
    // Install button: starts the installation in the background.
    void OnInstall(object s, EventArgs e)
    {
        if (done) { Close(); return; }
        installBtn.Enabled = cancelBtn.Enabled = false;
        desktopBox.Enabled = startupBox.Enabled = launchBox.Enabled = false;
        statusLabel.Visible = false; progress.Visible = log.Visible = true; progress.MarqueeAnimationSpeed = 30;
        bool desktop = desktopBox.Checked, startup = startupBox.Checked, launch = launchBox.Checked;
        new Thread(() => DoInstall(desktop, startup, launch)) { IsBackground = true }.Start();
    }

    // Adds a line to the log box.
    void Log(string line) { BeginInvoke((Action)(() => { log.AppendText(line + Environment.NewLine); })); }

    // Window mode: installs and updates the window when done.
    void DoInstall(bool desktop, bool startup, bool launch)
    {
        string error = Install(desktop, startup, launch, Log);
        FinishUi(error, desktop);
    }

    // Unpacks the app, runs install.ps1, cleans up and starts SlideLink if wanted. Returns an error or null.
    public static string Install(bool desktop, bool startup, bool launch, Action<string> Log)
    {
        string python = FindPython();
        if (python == null) return T("Python 3.10 or newer was not found.", "Python 3.10 oder neuer wurde nicht gefunden.");
        string dest = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "SlideLink");
        string work = Path.Combine(Path.GetTempPath(), "SlideLink-setup-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        string error = null;
        try
        {
            Log(T("Unpacking SlideLink…", "SlideLink wird entpackt…"));
            using (var res = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            using (var zip = new ZipArchive(res, ZipArchiveMode.Read))
                zip.ExtractToDirectory(work);
            Log(T("Installing…", "Wird installiert…"));
            string args = "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(work, "install.ps1") + "\" -NoLaunch -Python \"" + python +
                          "\" -Version \"" + Program.Version + "\"" + (desktop ? "" : " -NoDesktop") + (startup ? " -Startup" : "");
            var psi = new ProcessStartInfo("powershell.exe", args) { UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = work };
            using (var p = Process.Start(psi))
            {
                p.OutputDataReceived += (o, a) => { if (!string.IsNullOrWhiteSpace(a.Data)) Log(a.Data); };
                var err = new System.Text.StringBuilder();
                p.ErrorDataReceived += (o, a) => { if (!string.IsNullOrWhiteSpace(a.Data)) err.AppendLine(a.Data); };
                p.BeginOutputReadLine(); p.BeginErrorReadLine();
                p.WaitForExit();
                if (p.ExitCode != 0) error = err.Length > 0 ? err.ToString() : "install script failed (" + p.ExitCode + ")";
            }
            if (error == null && !startup)
                try { if (File.Exists(StartupLink())) File.Delete(StartupLink()); } catch { }
            if (error == null && launch)
            {
                Log(T("Starting SlideLink…", "SlideLink wird gestartet…"));
                Process.Start(new ProcessStartInfo(Path.Combine(Path.GetDirectoryName(python), "pythonw.exe"),
                    "\"" + Path.Combine(dest, "SlideLink.pyw") + "\"") { UseShellExecute = true, WorkingDirectory = dest });
            }
        }
        catch (Exception ex) { error = ex.Message; }
        finally { try { Directory.Delete(work, true); } catch { } }
        return error;
    }

    // Shows success (Finish) or the error in the window.
    void FinishUi(string error, bool desktop)
    {
        BeginInvoke((Action)(() =>
        {
            progress.MarqueeAnimationSpeed = 0; progress.Style = ProgressBarStyle.Continuous; progress.Value = error == null ? 100 : 0;
            if (error == null)
            {
                Log(T("Done. SlideLink is in your Start menu", "Fertig. SlideLink ist im Startmenü") + (desktop ? T(" and on your desktop.", " und auf dem Desktop.") : "."));
                installBtn.Text = T("Finish", "Fertig"); done = true;
            }
            else
            {
                Log("Error: " + error.Trim());
                MessageBox.Show(T("SlideLink could not be installed:\n\n", "SlideLink konnte nicht installiert werden:\n\n") + error.Trim(), "SlideLink Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                installBtn.Text = T("Try again", "Erneut versuchen");
                desktopBox.Enabled = startupBox.Enabled = launchBox.Enabled = true;
            }
            installBtn.Enabled = cancelBtn.Enabled = true;
        }));
    }
}
