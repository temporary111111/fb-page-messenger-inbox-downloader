using System.Diagnostics;
using System.Text;
using System.Text.Json;

ApplicationConfiguration.Initialize();
Application.Run(new MainForm(AppPaths.FromArgs(Environment.GetCommandLineArgs())));

sealed record AppPaths(string Root)
{
    public string Core => Path.Combine(Root, "InboxArchiveCore.exe");
    public string Env => Path.Combine(Root, ".env");
    public static AppPaths FromArgs(string[] args)
    {
        var selected = Array.IndexOf(args, "--app-dir");
        var root = selected >= 0 && selected + 1 < args.Length ? args[selected + 1] : AppContext.BaseDirectory;
        return new AppPaths(Path.GetFullPath(root));
    }
}

sealed class MainForm : Form
{
    readonly AppPaths paths;
    readonly Label phase = new() { AutoSize = true, Font = new Font("Segoe UI", 16, FontStyle.Bold) };
    readonly Label counts = new() { AutoSize = true, Font = new Font("Segoe UI", 12) };
    readonly Label detail = new() { AutoSize = true, ForeColor = Color.FromArgb(88, 96, 108), MaximumSize = new Size(620, 0) };
    readonly Button start = new() { Text = "Start sync", AutoSize = true };
    readonly Button stop = new() { Text = "Stop", AutoSize = true, Enabled = false };
    readonly Button export = new() { Text = "Export archive", AutoSize = true };
    readonly Button settings = new() { Text = "Settings", AutoSize = true };
    readonly System.Windows.Forms.Timer timer = new() { Interval = 3000 };
    Process? worker;

    public MainForm(AppPaths paths)
    {
        this.paths = paths;
        Text = "Inbox Archive";
        Size = new Size(700, 430);
        MinimumSize = new Size(620, 380);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.White;
        Font = new Font("Segoe UI", 10);
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(32), ColumnCount = 1, RowCount = 6 };
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.Controls.Add(new Label { Text = "Messenger inbox archive", AutoSize = true, Font = new Font("Segoe UI", 20, FontStyle.Bold) }, 0, 0);
        root.Controls.Add(phase, 0, 1);
        root.Controls.Add(counts, 0, 2);
        root.Controls.Add(detail, 0, 3);
        var actions = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.LeftToRight };
        actions.Controls.AddRange([start, stop, export, settings]);
        root.Controls.Add(actions, 0, 4);
        root.Controls.Add(new Label { Text = "Minimize keeps an app-started sync running. Closing this window stops only the sync it started.", AutoSize = true, ForeColor = Color.FromArgb(88, 96, 108) }, 0, 5);
        Controls.Add(root);
        start.Click += (_, _) => StartSync();
        stop.Click += (_, _) => StopSync();
        export.Click += async (_, _) => await ExportAsync();
        settings.Click += (_, _) => new SettingsForm(paths, RefreshStatus).ShowDialog(this);
        timer.Tick += async (_, _) => await RefreshStatus();
        FormClosing += (_, _) => StopSync();
        Shown += async (_, _) => { await RefreshStatus(); timer.Start(); };
    }

    ProcessStartInfo Info(params string[] args) => new(paths.Core, args)
    {
        WorkingDirectory = paths.Root, UseShellExecute = false, CreateNoWindow = true,
        RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8,
    };

    void StartSync()
    {
        if (!File.Exists(paths.Core)) { ShowError("Archive core is missing. Rebuild the portable folder."); return; }
        if (worker is { HasExited: false }) return;
        try
        {
            worker = Process.Start(Info("serve"));
            if (worker is null) throw new InvalidOperationException();
            worker.EnableRaisingEvents = true;
            worker.Exited += (_, _) => BeginInvoke(RefreshStatus);
            phase.Text = "Starting local sync…";
            start.Enabled = false; stop.Enabled = true;
        }
        catch { ShowError("Could not start the archive service. Check Settings and ensure no other archive process is using the port."); }
    }

    void StopSync()
    {
        if (worker is { HasExited: false })
        {
            worker.Kill(entireProcessTree: true);
            worker.WaitForExit(3000);
        }
        worker?.Dispose(); worker = null;
        start.Enabled = true; stop.Enabled = false;
    }

    async Task RefreshStatus()
    {
        if (!File.Exists(paths.Core)) { phase.Text = "Core not built"; counts.Text = ""; return; }
        try
        {
            using var probe = Process.Start(Info("status"));
            if (probe is null) return;
            var output = await probe.StandardOutput.ReadToEndAsync();
            await probe.WaitForExitAsync();
            if (probe.ExitCode != 0) throw new InvalidOperationException();
            using var json = JsonDocument.Parse(output);
            var data = json.RootElement;
            var syncing = worker is { HasExited: false };
            phase.Text = syncing ? "Sync is running" : "Sync is stopped";
            counts.Text = $"{data.GetProperty("conversations").GetInt32():N0} conversations   •   {data.GetProperty("messages").GetInt32():N0} messages   •   {data.GetProperty("downloaded_attachments").GetInt32():N0} files saved";
            var incomplete = data.GetProperty("incomplete_conversations").GetInt32();
            var sync = data.GetProperty("sync");
            var last = sync.TryGetProperty("last_sync_success", out var lastValue) ? lastValue.GetString() : "Not yet completed";
            detail.Text = incomplete > 0 ? $"Historical import is resumable. {incomplete:N0} conversations are still being checked. Last complete sync: {last}." : $"Archive is up to date. Last complete sync: {last}.";
            start.Enabled = !syncing; stop.Enabled = syncing;
        }
        catch
        {
            phase.Text = "Needs settings or attention";
            counts.Text = "";
            detail.Text = "Open Settings to verify your Page credentials and archive folder.";
        }
    }

    async Task ExportAsync()
    {
        export.Enabled = false; export.Text = "Exporting…";
        try
        {
            using var job = Process.Start(Info("export"));
            if (job is null) throw new InvalidOperationException();
            var output = await job.StandardOutput.ReadToEndAsync();
            await job.WaitForExitAsync();
            if (job.ExitCode != 0) throw new InvalidOperationException();
            MessageBox.Show(this, "Export created:\n" + output.Trim(), "Inbox Archive", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch { ShowError("Export failed. Check that the archive is accessible and has free disk space."); }
        finally { export.Enabled = true; export.Text = "Export archive"; }
    }
    void ShowError(string message) => MessageBox.Show(this, message, "Inbox Archive", MessageBoxButtons.OK, MessageBoxIcon.Warning);
}

sealed class SettingsForm : Form
{
    readonly AppPaths paths; readonly Func<Task> saved;
    readonly Dictionary<string, TextBox> fields = new();
    readonly string[] keys = ["META_PAGE_ID", "META_PAGE_ACCESS_TOKEN", "META_APP_SECRET", "WEBHOOK_VERIFY_TOKEN", "DATA_DIR", "POLL_SECONDS", "FULL_SCAN_HOURS", "MAX_ATTACHMENT_MB", "CLOUDFLARED_PATH"];
    public SettingsForm(AppPaths paths, Func<Task> saved)
    {
        this.paths = paths; this.saved = saved;
        Text = "Inbox Archive Settings"; Size = new Size(680, 560); StartPosition = FormStartPosition.CenterParent;
        var panel = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(20), AutoScroll = true, ColumnCount = 2 };
        var values = ReadEnv(paths.Env);
        foreach (var key in keys)
        {
            panel.Controls.Add(new Label { Text = key.Replace('_', ' '), AutoSize = true, Margin = new Padding(4, 10, 8, 4) });
            var box = new TextBox { Width = 390, Text = values.TryGetValue(key, out var value) ? value : "", UseSystemPasswordChar = key is "META_PAGE_ACCESS_TOKEN" or "META_APP_SECRET" or "WEBHOOK_VERIFY_TOKEN" };
            fields[key] = box; panel.Controls.Add(box);
        }
        var save = new Button { Text = "Save settings", AutoSize = true };
        save.Click += async (_, _) => { try { Save(); await saved(); Close(); } catch (Exception ex) { MessageBox.Show(this, ex.Message, "Settings", MessageBoxButtons.OK, MessageBoxIcon.Warning); } };
        panel.Controls.Add(save); Controls.Add(panel);
    }
    static Dictionary<string, string> ReadEnv(string path) => File.Exists(path) ? File.ReadLines(path).Where(x => x.Contains('=') && !x.TrimStart().StartsWith('#')).Select(x => x.Split('=', 2)).ToDictionary(x => x[0].Trim(), x => x[1].Trim()) : new();
    void Save()
    {
        if (string.IsNullOrWhiteSpace(fields["META_PAGE_ID"].Text) || !fields["META_PAGE_ID"].Text.All(char.IsDigit)) throw new InvalidOperationException("Page ID must contain numbers only.");
        if (keys.Take(4).Any(key => string.IsNullOrWhiteSpace(fields[key].Text))) throw new InvalidOperationException("Fill Page ID, access token, app secret, and verify token.");
        var original = ReadEnv(paths.Env); foreach (var pair in fields) original[pair.Key] = pair.Value.Text.Trim();
        original.TryAdd("META_API_VERSION", "v27.0"); original.TryAdd("PORT", "8787");
        var temp = paths.Env + ".tmp";
        File.WriteAllLines(temp, original.Select(pair => pair.Key + "=" + pair.Value));
        File.Move(temp, paths.Env, true);
    }
}
