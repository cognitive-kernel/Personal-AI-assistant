using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;
using System.Linq;
using System.Web.Script.Serialization;

internal sealed class AutonomousStateBrokerService : ServiceBase
{
    private const string PipeName = "PersonalAIAssistant.StateBroker.v1";
    private const int WorkerCount = 8;
    private const int MaxRequestBytes = 16384;
    private const int MaxDiagnosticBytes = 240000;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetNamedPipeClientComputerName(IntPtr pipe, StringBuilder computerName, uint length);
    private readonly object _healthGate = new object();
    private readonly JavaScriptSerializer _json = new JavaScriptSerializer { MaxJsonLength = 12 * 1024 * 1024 };
    private volatile bool _stopping;
    private string _stateDirectory;
    private bool _lastHealth;
    private DateTime _lastHealthAt = DateTime.MinValue;
    private Thread[] _workers;

    public AutonomousStateBrokerService()
    {
        ServiceName = "PersonalAIAssistantStateBroker";
        CanStop = true;
        AutoLog = true;
    }

    protected override void OnStart(string[] args)
    {
        _stateDirectory = ResolveStateDirectory();
        _stopping = false;
        _workers = new Thread[WorkerCount];
        for (int i = 0; i < _workers.Length; i++)
        {
            _workers[i] = new Thread(WorkerLoop);
            _workers[i].IsBackground = true;
            _workers[i].Name = "AutonomousStateBroker.Pipe." + i;
            _workers[i].Start();
        }
    }

    protected override void OnStop()
    {
        _stopping = true;
        // Closing the service process or stopping the service releases all pipe
        // handles and the OS byte-range lock if a worker was in a state operation.
        if (_workers != null)
            foreach (Thread worker in _workers)
                if (worker != null && worker.IsAlive)
                    worker.Join(TimeSpan.FromSeconds(2));
    }

    private static string ResolveStateDirectory()
    {
        string configured = Environment.GetEnvironmentVariable(
            "AUTONOMOUS_ENGINEERING_PROGRESS_DIR",
            EnvironmentVariableTarget.Machine
        );
        string candidate;
        if (!String.IsNullOrWhiteSpace(configured))
        {
            candidate = Environment.ExpandEnvironmentVariables(configured.Trim());
            if (!Path.IsPathRooted(candidate))
                throw new InvalidOperationException("Machine state directory must be absolute.");
        }
        else
        {
            string commonData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            if (String.IsNullOrWhiteSpace(commonData) || !Path.IsPathRooted(commonData))
                throw new InvalidOperationException("The machine application-data directory is unavailable.");
            candidate = Path.Combine(commonData, "PersonalAIAssistant", "autonomous-engineering");
        }

        string full = Path.GetFullPath(candidate);
        DirectoryInfo info = new DirectoryInfo(full);
        if (!info.Exists)
            throw new DirectoryNotFoundException("The provisioned autonomous-engineering state directory is missing.");
        if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("The shared state root cannot be a reparse point.");
        return full;
    }

    private static PipeSecurity CreatePipeSecurity()
    {
        PipeSecurity security = new PipeSecurity();
        security.SetAccessRuleProtection(true, false);
        // The pipe exposes a narrow, allow-listed application protocol only.
        // It does not grant clients filesystem access to the protected state.
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier("S-1-5-11"),
            PipeAccessRights.ReadWrite,
            AccessControlType.Allow
        ));
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier("S-1-5-18"),
            PipeAccessRights.FullControl,
            AccessControlType.Allow
        ));
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier("S-1-5-32-544"),
            PipeAccessRights.FullControl,
            AccessControlType.Allow
        ));
        return security;
    }

    private void WorkerLoop()
    {
        while (!_stopping)
        {
            try
            {
                using (NamedPipeServerStream pipe = new NamedPipeServerStream(
                    PipeName,
                    PipeDirection.InOut,
                    WorkerCount,
                    PipeTransmissionMode.Byte,
                    PipeOptions.None,
                    4096,
                    4096,
                    CreatePipeSecurity()
                ))
                {
                    pipe.WaitForConnection();
                    if (_stopping) return;
                    HandleClient(pipe);
                }
            }
            catch (ObjectDisposedException) { if (_stopping) return; }
            catch (IOException) { if (_stopping) return; }
            catch (Exception ex)
            {
                try { EventLog.WriteEntry("PersonalAIAssistantStateBroker: " + ex.GetType().Name, EventLogEntryType.Warning); } catch { }
                Thread.Sleep(200);
            }
        }
    }

    private void HandleClient(NamedPipeServerStream pipe)
    {
        StringBuilder clientMachine = new StringBuilder(256);
        if (!GetNamedPipeClientComputerName(pipe.SafePipeHandle.DangerousGetHandle(), clientMachine, (uint)clientMachine.Capacity) ||
            !String.Equals(clientMachine.ToString(), Environment.MachineName, StringComparison.OrdinalIgnoreCase))
            throw new UnauthorizedAccessException("Remote named-pipe clients are not accepted.");

        string callerSid = null;
        pipe.RunAsClient(delegate
        {
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
            {
                if (identity.User == null || identity.IsAnonymous || identity.IsGuest)
                    throw new UnauthorizedAccessException("An authenticated local Windows identity is required.");
                callerSid = identity.User.Value;
            }
        });
        if (String.IsNullOrWhiteSpace(callerSid))
            throw new UnauthorizedAccessException("The pipe client identity could not be verified.");

        string line = ReadBoundedLine(pipe, MaxRequestBytes);
        if (line == null) return;
        Dictionary<string, object> request = _json.DeserializeObject(line) as Dictionary<string, object>;
        if (request == null ||
            !request.ContainsKey("version") || Convert.ToInt32(request["version"]) != 1 ||
            !request.ContainsKey("method") || !request.ContainsKey("path"))
        {
            WriteResponse(pipe, 400, Error("invalid_request"));
            return;
        }

        string method = Convert.ToString(request["method"]).ToUpperInvariant();
        string path = Convert.ToString(request["path"]);
        if (request.ContainsKey("body"))
        {
            WriteResponse(pipe, 400, Error("request_body_not_supported"));
            return;
        }

        if (method == "GET" && path == "/v1/health")
        {
            bool ready = VerifyStorageAccessCached();
            WriteResponse(pipe, ready ? 200 : 503, new Dictionary<string, object> {
                { "ok", true }, { "ready", ready }, { "service", ServiceName }, { "schema_version", 1 }
            });
            return;
        }
        if (method == "GET" && path == "/v1/pending-work")
        {
            WriteResponse(pipe, 200, ReadPendingWork());
            return;
        }
        if (method == "GET" && path == "/v1/progress")
        {
            WriteResponse(pipe, 200, ReadProgress());
            return;
        }
        if (method == "GET" && path.StartsWith("/v1/diagnostics/", StringComparison.Ordinal))
        {
            string runId = path.Substring("/v1/diagnostics/".Length);
            long parsed;
            if (runId.Length > 20 || !Int64.TryParse(runId, out parsed) || parsed <= 0)
            {
                WriteResponse(pipe, 400, Error("invalid_run_id"));
                return;
            }
            object diagnostics = ReadDiagnostics(runId);
            WriteResponse(pipe, diagnostics == null ? 404 : 200, diagnostics ?? Error("not_found"));
            return;
        }
        if (method == "POST" && path == "/v1/cleanup")
        {
            WriteResponse(pipe, 200, CleanupProgress());
            return;
        }

        WriteResponse(pipe, 404, Error("not_found"));
    }

    private static string ReadBoundedLine(Stream stream, int maximumBytes)
    {
        using (MemoryStream buffer = new MemoryStream())
        {
            while (buffer.Length <= maximumBytes)
            {
                int value = stream.ReadByte();
                if (value < 0) return null;
                if (value == 10) return Encoding.UTF8.GetString(buffer.ToArray()).TrimEnd('\r');
                if (value != 13) buffer.WriteByte((byte)value);
            }
        }
        throw new InvalidDataException("The local state-broker request exceeded its size limit.");
    }

    private static void WriteResponse(Stream stream, int status, object payload)
    {
        Dictionary<string, object> envelope = new Dictionary<string, object> {
            { "version", 1 }, { "status", status }, { "payload", payload }
        };
        string json = new JavaScriptSerializer { MaxJsonLength = 12 * 1024 * 1024 }.Serialize(envelope);
        byte[] bytes = new UTF8Encoding(false).GetBytes(json + "\n");
        stream.Write(bytes, 0, bytes.Length);
        stream.Flush();
    }

    private static Dictionary<string, object> Error(string code)
    {
        return new Dictionary<string, object> { { "error", code } };
    }

    private bool VerifyStorageAccessCached()
    {
        lock (_healthGate)
        {
            if (DateTime.UtcNow - _lastHealthAt < TimeSpan.FromSeconds(15))
                return _lastHealth;
            _lastHealth = VerifyStorageAccess();
            _lastHealthAt = DateTime.UtcNow;
            return _lastHealth;
        }
    }

    private bool VerifyStorageAccess()
    {
        try
        {
            if (!Directory.Exists(_stateDirectory)) return false;
            DirectoryInfo info = new DirectoryInfo(_stateDirectory);
            if ((info.Attributes & FileAttributes.ReparsePoint) != 0) return false;
            string probe = Path.Combine(_stateDirectory, ".state-broker-probe-" + Guid.NewGuid().ToString("N") + ".tmp");
            using (FileStream file = new FileStream(probe, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None))
            {
                file.WriteByte(0x41);
                file.Flush(true);
            }
            File.Delete(probe);
            return true;
        }
        catch { return false; }
    }

    private object ReadPendingWork()
    {
        return WithStateLock(delegate
        {
            Dictionary<string, bool> markers = new Dictionary<string, bool>();
            string[] keys = { "pending_task", "pending_owner", "pending_pr", "cleanup_in_progress" };
            string[] names = {
                "pending-manual-task.txt",
                "pending-manual-task-owner.json",
                "pending-autonomous-pr.json",
                "pending-state-cleanup.json"
            };
            List<string> blockers = new List<string>();
            for (int i = 0; i < names.Length; i++)
            {
                bool exists = SafeFileExists(Path.Combine(_stateDirectory, names[i]));
                markers[keys[i]] = exists;
                if (exists) blockers.Add(keys[i]);
            }
            return new Dictionary<string, object> {
                { "available", true },
                { "blocked", blockers.Count > 0 },
                { "markers", markers },
                { "reason", blockers.Count > 0 ? String.Join(", ", blockers.ToArray()) : null }
            };
        }, 2000);
    }

    private object ReadProgress()
    {
        return new Dictionary<string, object> {
            { "current", ReadJsonFile("current.json", 256000) },
            { "baseline", ReadJsonFile("scorecard-baseline.json", 256000) },
            { "history", ReadJsonLines("history.jsonl", 8 * 1024 * 1024, 20000, "project_snapshot") },
            { "events", ReadProgressEvents() },
            { "storage_configured", Directory.Exists(_stateDirectory) },
            { "updated_at", DateTime.UtcNow.ToString("o") }
        };
    }

    private object ReadProgressEvents()
    {
        List<object> values = ReadJsonLines("events.jsonl", 1024 * 1024, 1000, "progress_event");
        List<object> result = new List<object>();
        int start = Math.Max(0, values.Count - 50);
        for (int i = start; i < values.Count; i++)
        {
            Dictionary<string, object> item = values[i] as Dictionary<string, object>;
            if (item == null) continue;
            Dictionary<string, object> safe = new Dictionary<string, object>();
            foreach (string key in new[] { "updated_at", "phase_label", "activity", "percent", "status" })
                if (item.ContainsKey(key)) safe[key] = item[key];
            result.Add(safe);
        }
        return result;
    }

    private object ReadJsonFile(string name, int maximumBytes)
    {
        string path = Path.Combine(_stateDirectory, name);
        if (!SafeFileExists(path)) return null;
        FileInfo info = new FileInfo(path);
        if (info.Length > maximumBytes) return null;
        string raw = File.ReadAllText(path, Encoding.UTF8);
        try { return new JavaScriptSerializer { MaxJsonLength = maximumBytes + 1024 }.DeserializeObject(raw); }
        catch { return null; }
    }

    private List<object> ReadJsonLines(string name, int maximumBytes, int maximumLines, string requiredType)
    {
        List<object> values = new List<object>();
        string path = Path.Combine(_stateDirectory, name);
        if (!SafeFileExists(path)) return values;
        FileInfo info = new FileInfo(path);
        if (info.Length > maximumBytes) return values;
        string[] lines = File.ReadAllLines(path, Encoding.UTF8);
        int start = Math.Max(0, lines.Length - maximumLines);
        JavaScriptSerializer serializer = new JavaScriptSerializer { MaxJsonLength = maximumBytes + 1024 };
        for (int i = start; i < lines.Length; i++)
        {
            if (String.IsNullOrWhiteSpace(lines[i])) continue;
            try
            {
                Dictionary<string, object> item = serializer.DeserializeObject(lines[i]) as Dictionary<string, object>;
                object type;
                if (item != null && item.TryGetValue("type", out type) && String.Equals(Convert.ToString(type), requiredType, StringComparison.Ordinal))
                    values.Add(item);
            }
            catch { }
        }
        return values;
    }

    private object ReadDiagnostics(string runId)
    {
        string path = Path.Combine(_stateDirectory, "run-diagnostics", runId + ".json");
        if (!SafeFileExists(path)) return null;
        FileInfo info = new FileInfo(path);
        if (info.Length > MaxDiagnosticBytes) return null;
        string raw = File.ReadAllText(path, Encoding.UTF8);
        try
        {
            Dictionary<string, object> payload = new JavaScriptSerializer { MaxJsonLength = MaxDiagnosticBytes + 1024 }.DeserializeObject(raw) as Dictionary<string, object>;
            object schema, savedId, entries;
            if (payload == null || !payload.TryGetValue("schema_version", out schema) ||
                Convert.ToInt32(schema) != 1 || !payload.TryGetValue("run_id", out savedId) ||
                Convert.ToString(savedId) != runId || !payload.TryGetValue("diagnostics", out entries) ||
                !(entries is object[])) return null;

            object[] sourceEntries = (object[])entries;
            List<object> safeEntries = new List<object>();
            int start = Math.Max(0, sourceEntries.Length - 8);
            string[] stringFields = { "run_attempt", "stage", "category", "summary", "detail", "recorded_at" };
            for (int index = start; index < sourceEntries.Length; index++)
            {
                object value = sourceEntries[index];
            {
                Dictionary<string, object> entry = value as Dictionary<string, object>;
                if (entry == null) continue;
                Dictionary<string, object> safe = new Dictionary<string, object>();
                foreach (string field in stringFields)
                {
                    object rawValue;
                    if (entry.TryGetValue(field, out rawValue))
                    {
                        int limit = field == "detail" ? 12000 : (field == "summary" ? 800 : 100);
                        string text = Convert.ToString(rawValue) ?? "";
                        safe[field] = text.Length > limit ? text.Substring(0, limit) : text;
                    }
                }
                foreach (string field in new[] { "task", "attempt" })
                {
                    object rawValue;
                    if (entry.TryGetValue(field, out rawValue) && rawValue is Int32)
                        safe[field] = rawValue;
                }
                safeEntries.Add(safe);
            }
            return new Dictionary<string, object> {
                { "schema_version", 1 },
                { "run_id", runId },
                { "updated_at", payload.ContainsKey("updated_at") ? payload["updated_at"] : "" },
                { "diagnostics", safeEntries.ToArray() }
            };
        }
        catch { return null; }
    }

    private object CleanupProgress()
    {
        return WithStateLock(delegate
        {
            string[] markerNames = {
                "pending-manual-task.txt",
                "pending-manual-task-owner.json",
                "pending-autonomous-pr.json",
                "pending-state-cleanup.json"
            };
            List<string> blockers = new List<string>();
            foreach (string name in markerNames)
                if (SafeFileExists(Path.Combine(_stateDirectory, name))) blockers.Add(name);
            if (blockers.Count > 0)
                return new Dictionary<string, object> {
                    { "conflict", true }, { "blockers", blockers.ToArray() },
                    { "message", "Durable task state is pending; cleanup was refused." }
                };

            string[] cleanupNames = {
                "events.jsonl",
                "current.json",
                "events.jsonl.tmp",
                "current.json.tmp",
                "history.jsonl.tmp",
                "scorecard-state.json.tmp",
                "scorecard-baseline.json.tmp"
            };
            int removed = 0;
            long removedBytes = 0;
            List<string> failed = new List<string>();
            foreach (string name in cleanupNames)
            {
                string path = Path.Combine(_stateDirectory, name);
                try
                {
                    if (!SafeFileExists(path)) continue;
                    long size = new FileInfo(path).Length;
                    File.Delete(path);
                    removed++;
                    removedBytes += size;
                }
                catch (FileNotFoundException) { }
                catch { failed.Add(name); }
            }
            return new Dictionary<string, object> {
                { "conflict", false },
                { "cleaned", failed.Count == 0 },
                { "files_removed", removed },
                { "bytes_removed", removedBytes },
                { "failed_files", failed.ToArray() },
                { "preserved", new[] { "history.jsonl", "scorecard-baseline.json", "checkpoint files", "pending task/PR markers" } }
            };
        }, 2000);
    }

    private object WithStateLock(Func<object> operation, int timeoutMilliseconds)
    {
        string lockPath = Path.Combine(_stateDirectory, "autonomous-durable-state.lock");
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        using (FileStream stream = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite))
        {
            if (stream.Length == 0)
            {
                stream.WriteByte(0);
                stream.Flush(true);
            }
            bool owned = false;
            while (!owned)
            {
                try { stream.Lock(0, 1); owned = true; }
                catch (IOException)
                {
                    if (DateTime.UtcNow >= deadline) throw new TimeoutException("Shared state lock timeout.");
                    Thread.Sleep(50);
                }
            }
            try { return operation(); }
            finally { try { stream.Unlock(0, 1); } catch { } }
        }
    }

    private static bool SafeFileExists(string path)
    {
        try
        {
            FileAttributes attributes = File.GetAttributes(path);
            if ((attributes & FileAttributes.ReparsePoint) != 0 || (attributes & FileAttributes.Directory) != 0)
                throw new IOException("State marker is not a regular file.");
            return true;
        }
        catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
    }
}

internal static class Program
{
    private static void Main()
    {
        ServiceBase.Run(new AutonomousStateBrokerService());
    }
}
