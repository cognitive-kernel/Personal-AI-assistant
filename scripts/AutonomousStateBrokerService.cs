using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Linq;
using System.Web.Script.Serialization;

internal sealed class AutonomousStateBrokerService : ServiceBase
{
    private const string PipeName = "PersonalAIAssistant.StateBroker.v1";
    private const int WorkerCount = 8;
    private const int ErrorPipeLocal = 229;
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
        // Network logons are denied at the pipe ACL before they can occupy workers.
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier("S-1-5-2"),
            PipeAccessRights.ReadWrite,
            AccessControlType.Deny
        ));
        // The pipe exposes a narrow, allow-listed application protocol only.
        // It does not grant clients filesystem access to the protected state.
        // LocalService owns the server process and must be allowed to create
        // named-pipe instances. ReadWrite alone does not include CreateNewInstance.
        security.AddAccessRule(new PipeAccessRule(
            new SecurityIdentifier("S-1-5-19"),
            PipeAccessRights.CreateNewInstance | PipeAccessRights.ReadWrite,
            AccessControlType.Allow
        ));
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
                    PipeOptions.Asynchronous,
                    4096,
                    4096,
                    CreatePipeSecurity()
                ))
                {
                    pipe.WaitForConnectionAsync().GetAwaiter().GetResult();
                    if (_stopping) return;
                    HandleClient(pipe);
                }
            }
            catch (ObjectDisposedException) { if (_stopping) return; }
            catch (IOException) { if (_stopping) return; }
            catch (Exception ex)
            {
                LogWorkerException(ex);
                try { EventLog.WriteEntry("PersonalAIAssistantStateBroker: " + ex.ToString(), EventLogEntryType.Warning); } catch { }
                Thread.Sleep(200);
            }
        }
    }

    private void HandleClient(NamedPipeServerStream pipe)
    {
        StringBuilder clientMachine = new StringBuilder(256);
        bool clientNameResolved = GetNamedPipeClientComputerName(
            pipe.SafePipeHandle.DangerousGetHandle(),
            clientMachine,
            (uint)(clientMachine.Capacity * sizeof(char))
        );
        if (clientNameResolved)
            throw new UnauthorizedAccessException("Remote named-pipe clients are not accepted.");

        int clientNameError = Marshal.GetLastWin32Error();
        if (clientNameError != ErrorPipeLocal)
            throw new UnauthorizedAccessException(
                "Could not prove that the named-pipe client is local (WinError " + clientNameError + ")."
            );

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
            !request.ContainsKey("method") || !request.ContainsKey("path") ||
            request.Keys.Any(key => key != "version" && key != "method" && key != "path" && key != "body"))
        {
            WriteResponse(pipe, 400, Error("invalid_request"));
            return;
        }

        string method = Convert.ToString(request["method"]).ToUpperInvariant();
        string path = Convert.ToString(request["path"]);
        object rawBody;
        Dictionary<string, object> body = request.TryGetValue("body", out rawBody)
            ? rawBody as Dictionary<string, object> : null;
        bool taskEndpoint = path == "/v1/tasks/pending" || path == "/v1/tasks/archived" ||
            path == "/v1/tasks/archive" || path == "/v1/tasks/restore";
        if (request.ContainsKey("body") && (!taskEndpoint || body == null))
        {
            WriteResponse(pipe, 400, Error("request_body_not_supported"));
            return;
        }
        if (taskEndpoint)
        {
            object taskResult;
            int taskStatus;
            HandlePendingTaskRequest(method, path, body, out taskStatus, out taskResult);
            WriteResponse(pipe, taskStatus, taskResult);
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
            Dictionary<string, object> cleanup = CleanupProgress() as Dictionary<string, object>;
            object conflict;
            int status = cleanup != null && cleanup.TryGetValue("conflict", out conflict) &&
                conflict is Boolean && (Boolean)conflict ? 409 : 200;
            WriteResponse(pipe, status, cleanup ?? Error("cleanup_failed"));
            return;
        }

        WriteResponse(pipe, 404, Error("not_found"));
    }


    private static string Sha256(byte[] value)
    {
        using (SHA256 hash = SHA256.Create())
        {
            byte[] digest = hash.ComputeHash(value);
            StringBuilder result = new StringBuilder(digest.Length * 2);
            foreach (byte item in digest) result.Append(item.ToString("x2"));
            return result.ToString();
        }
    }

    private static byte[] CanonicalTaskBytes(byte[] value)
    {
        string text = new UTF8Encoding(false, true).GetString(value);
        return Encoding.UTF8.GetBytes(text.Replace("\r\n", "\n").Replace("\r", "\n"));
    }

    private Dictionary<string, object> ReadPendingTaskRecord()
    {
        string taskPath = Path.Combine(_stateDirectory, "pending-manual-task.txt");
        string ownerPath = Path.Combine(_stateDirectory, "pending-manual-task-owner.json");
        if (!SafeFileExists(taskPath)) return null;
        FileInfo taskInfo = new FileInfo(taskPath);
        if (taskInfo.Length < 1 || taskInfo.Length > 8192)
            throw new InvalidDataException("Pending task size is invalid.");
        byte[] taskBytes = File.ReadAllBytes(taskPath);
        byte[] ownerBytes;
        try { ownerBytes = File.ReadAllBytes(ownerPath); }
        catch { throw new InvalidDataException("Pending task owner metadata is missing or unreadable."); }
        if (ownerBytes.Length > 65536)
            throw new InvalidDataException("Pending task owner metadata is oversized.");
        Dictionary<string, object> owner;
        try { owner = new JavaScriptSerializer().DeserializeObject(
            new UTF8Encoding(false, true).GetString(ownerBytes)) as Dictionary<string, object>; }
        catch { throw new InvalidDataException("Pending task owner metadata is malformed."); }
        if (owner == null) throw new InvalidDataException("Pending task owner metadata is invalid.");
        object schema, taskHash, ownerRun, ownerAttempt, submittedRun, submittedAttempt, createdAt, claimedAt, count, source;
        if (!owner.TryGetValue("schema_version", out schema) || Convert.ToInt32(schema) != 1 ||
            !owner.TryGetValue("task_sha256", out taskHash) ||
            !String.Equals(Convert.ToString(taskHash), Sha256(CanonicalTaskBytes(taskBytes)), StringComparison.Ordinal) ||
            !owner.TryGetValue("owner_run_id", out ownerRun) || String.IsNullOrWhiteSpace(Convert.ToString(ownerRun)) ||
            !owner.TryGetValue("owner_attempt", out ownerAttempt) || Convert.ToInt32(ownerAttempt) < 1 ||
            !owner.TryGetValue("submitted_run_id", out submittedRun) || String.IsNullOrWhiteSpace(Convert.ToString(submittedRun)) ||
            !owner.TryGetValue("submitted_attempt", out submittedAttempt) || Convert.ToInt32(submittedAttempt) < 1 ||
            !owner.TryGetValue("created_at", out createdAt) || !owner.TryGetValue("claimed_at", out claimedAt) ||
            !owner.TryGetValue("claim_count", out count) || Convert.ToInt32(count) < 1 ||
            !owner.TryGetValue("source", out source) ||
            !new[] { "manual_dispatch", "recovery", "legacy_migration" }.Contains(Convert.ToString(source)))
            throw new InvalidDataException("Pending task owner metadata does not match schema v1.");
        string taskDigest = Sha256(taskBytes);
        byte[] identityBytes = new byte[taskBytes.Length + 1 + ownerBytes.Length];
        Buffer.BlockCopy(taskBytes, 0, identityBytes, 0, taskBytes.Length);
        Buffer.BlockCopy(ownerBytes, 0, identityBytes, taskBytes.Length + 1, ownerBytes.Length);
        string taskId = Sha256(identityBytes);
        List<string> blockers = PendingTaskBlockers();
        blockers.Sort(StringComparer.Ordinal);
        string revision = Sha256(Encoding.UTF8.GetBytes(
            taskDigest + "|" + Sha256(ownerBytes) + "|" + String.Join(",", blockers.ToArray())
        ));
        return new Dictionary<string, object> {
            { "task_id", taskId }, { "revision", revision }, { "task_bytes", taskBytes },
            { "owner_bytes", ownerBytes }, { "owner", owner }, { "blockers", blockers }
        };
    }

    private List<string> PendingTaskBlockers()
    {
        List<string> blockers = new List<string>();
        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-autonomous-pr.json")))
            blockers.Add("pending_pr");
        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-state-cleanup.json")))
            blockers.Add("cleanup_in_progress");
        if (SafeFileExists(Path.Combine(_stateDirectory, "latest-worktree-checkpoint.json")) ||
            Directory.GetFiles(_stateDirectory, "worktree-checkpoint-*").Length > 0 ||
            Directory.GetFiles(_stateDirectory, "worktree-plan-*").Length > 0)
            blockers.Add("checkpoint_present_or_unknown");
        string currentPath = Path.Combine(_stateDirectory, "current.json");
        if (SafeFileExists(currentPath))
        {
            Dictionary<string, object> current = ReadJsonFile("current.json", 256000) as Dictionary<string, object>;
            object rawStatus;
            string status = current != null && current.TryGetValue("status", out rawStatus)
                ? Convert.ToString(rawStatus).Trim().ToLowerInvariant() : "";
            string[] terminal = { "completed", "succeeded", "success", "complete", "failed", "cancelled", "skipped", "blocked" };
            if (current == null || !Array.Exists(terminal, item => item == status))
                blockers.Add("active_or_unknown_run");
        }
        return blockers;
    }

    private object ListPendingTasks()
    {
        return WithStateLock(delegate
        {
            Dictionary<string, object> record = ReadPendingTaskRecord();
            if (record == null) return new Dictionary<string, object> {
                { "available", true }, { "tasks", new object[0] }
            };
            Dictionary<string, object> owner = (Dictionary<string, object>)record["owner"];
            return new Dictionary<string, object> {
                { "available", true },
                { "tasks", new object[] { new Dictionary<string, object> {
                    { "task_id", record["task_id"] }, { "revision", record["revision"] },
                    { "status", "pending" }, { "created_at", owner["created_at"] },
                    { "owner_run_id", owner["owner_run_id"] }, { "owner_attempt", owner["owner_attempt"] },
                    { "source", owner["source"] }, { "blocked", ((List<string>)record["blockers"]).Count > 0 },
                    { "blockers", ((List<string>)record["blockers"]).ToArray() }
                }}}
            };
        }, 2000);
    }

    private object ListArchivedTasks()
    {
        return WithStateLock(delegate
        {
            string root = Path.Combine(_stateDirectory, "archived-pending-tasks");
            if (!Directory.Exists(root)) Directory.CreateDirectory(root);
            List<object> tasks = new List<object>();
            foreach (string directory in Directory.GetDirectories(root))
            {
                string id = Path.GetFileName(directory);
                if (!System.Text.RegularExpressions.Regex.IsMatch(id, "^[a-f0-9]{64}$"))
                    throw new InvalidDataException("Archive contains an unknown entry.");
                string taskPath = Path.Combine(directory, "task.txt");
                string ownerPath = Path.Combine(directory, "owner.json");
                string manifestPath = Path.Combine(directory, "manifest.json");
                if (!SafeFileExists(taskPath) || !SafeFileExists(ownerPath) || !SafeFileExists(manifestPath))
                    throw new InvalidDataException("Archived task package is incomplete.");
                byte[] task = File.ReadAllBytes(taskPath), owner = File.ReadAllBytes(ownerPath);
                Dictionary<string, object> manifest = ReadJsonFile(Path.Combine("archived-pending-tasks", id, "manifest.json"), 65536) as Dictionary<string, object>;
                if (manifest == null || Convert.ToString(manifest.ContainsKey("task_id") ? manifest["task_id"] : "") != id ||
                    Convert.ToString(manifest.ContainsKey("task_sha256") ? manifest["task_sha256"] : "") != Sha256(task) ||
                    Convert.ToString(manifest.ContainsKey("owner_sha256") ? manifest["owner_sha256"] : "") != Sha256(owner))
                    throw new InvalidDataException("Archived task manifest is inconsistent.");
                string archivedAt = Convert.ToString(manifest.ContainsKey("archived_at_ms") ? manifest["archived_at_ms"] : "", System.Globalization.CultureInfo.InvariantCulture);
                string restoredAt = Convert.ToString(manifest.ContainsKey("restored_at_ms") ? manifest["restored_at_ms"] : "", System.Globalization.CultureInfo.InvariantCulture);
                string revision = Sha256(Encoding.UTF8.GetBytes(
                    Sha256(task) + "|" + Sha256(owner) + "|" + archivedAt + "|" + restoredAt
                ));
                Dictionary<string, object> ownerObject = new JavaScriptSerializer().DeserializeObject(
                    Encoding.UTF8.GetString(owner)) as Dictionary<string, object>;
                tasks.Add(new Dictionary<string, object> {
                    { "task_id", id }, { "revision", revision },
                    { "status", manifest.ContainsKey("restored_at_ms") ? "restored" : "archived" },
                    { "created_at", ownerObject != null && ownerObject.ContainsKey("created_at") ? ownerObject["created_at"] : null },
                    { "archived_at", manifest.ContainsKey("archived_at_ms") ? manifest["archived_at_ms"] : null },
                    { "restorable", !manifest.ContainsKey("restored_at_ms") }
                });
            }
            return new Dictionary<string, object> { { "available", true }, { "tasks", tasks.ToArray() } };
        }, 2000);
    }

    private void WriteAtomic(string path, byte[] bytes)
    {
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }
        finally { try { if (File.Exists(temporary)) File.Delete(temporary); } catch { } }
    }

    private object MutatePendingTask(string method, Dictionary<string, object> body)
    {
        string taskId = Convert.ToString(body["task_id"]);
        string revision = Convert.ToString(body["revision"]);
        return WithRepositoryExecutionLock(delegate
        {
            return WithStateLock(delegate
            {
                return WithCheckpointStoreLock(delegate
                {
                    if (method == "POST" && body.ContainsKey("task_id"))
                    {
                        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-state-cleanup.json")))
                            throw new InvalidDataException("cleanup_in_progress");
                        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-autonomous-pr.json")))
                            throw new InvalidDataException("pending_pr");
                        if (SafeFileExists(Path.Combine(_stateDirectory, "latest-worktree-checkpoint.json")) ||
                            Directory.GetFiles(_stateDirectory, "worktree-checkpoint-*").Length > 0 ||
                            Directory.GetFiles(_stateDirectory, "worktree-plan-*").Length > 0)
                            throw new InvalidDataException("checkpoint_present_or_unknown");
                        Dictionary<string, object> record = ReadPendingTaskRecord();
                        if (record == null || Convert.ToString(record["task_id"]) != taskId)
                            throw new InvalidDataException("stale_task");
                        if (Convert.ToString(record["revision"]) != revision)
                            throw new InvalidDataException("stale_revision");
                        List<string> blockers = (List<string>)record["blockers"];
                        if (blockers.Count > 0) throw new InvalidDataException(String.Join(",", blockers.ToArray()));
                        string root = Path.Combine(_stateDirectory, "archived-pending-tasks");
                        Directory.CreateDirectory(root);
                        string final = Path.Combine(root, taskId);
                        if (!Directory.Exists(final))
                        {
                            string stage = Path.Combine(root, ".stage-" + Guid.NewGuid().ToString("N"));
                            Directory.CreateDirectory(stage);
                            WriteAtomic(Path.Combine(stage, "task.txt"), (byte[])record["task_bytes"]);
                            WriteAtomic(Path.Combine(stage, "owner.json"), (byte[])record["owner_bytes"]);
                            Dictionary<string, object> manifest = new Dictionary<string, object> {
                                { "schema_version", 1 }, { "task_id", taskId }, { "archived_at_ms", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() },
                                { "task_sha256", Sha256((byte[])record["task_bytes"]) },
                                { "owner_sha256", Sha256((byte[])record["owner_bytes"]) }
                            };
                            WriteAtomic(Path.Combine(stage, "manifest.json"), Encoding.UTF8.GetBytes(_json.Serialize(manifest) + "\n"));
                            Directory.Move(stage, final);
                        }
                        File.Delete(Path.Combine(_stateDirectory, "pending-manual-task.txt"));
                        File.Delete(Path.Combine(_stateDirectory, "pending-manual-task-owner.json"));
                        return new Dictionary<string, object> { { "task_id", taskId }, { "status", "archived" }, { "preserved", true } };
                    }
                    throw new InvalidDataException("unsupported_task_operation");
                }, 250);
            }, 2000);
        }, 250);
    }


    private object RestoreArchivedTask(Dictionary<string, object> body)
    {
        string taskId = Convert.ToString(body["task_id"]);
        string revision = Convert.ToString(body["revision"]);
        return WithRepositoryExecutionLock(delegate
        {
            return WithStateLock(delegate
            {
                return WithCheckpointStoreLock(delegate
                {
                    string root = Path.Combine(_stateDirectory, "archived-pending-tasks");
                    string package = Path.Combine(root, taskId);
                    string taskPath = Path.Combine(package, "task.txt");
                    string ownerPath = Path.Combine(package, "owner.json");
                    string manifestPath = Path.Combine(package, "manifest.json");
                    if (!Directory.Exists(package) || !SafeFileExists(taskPath) ||
                        !SafeFileExists(ownerPath) || !SafeFileExists(manifestPath))
                        throw new InvalidDataException("stale_task");
                    if (SafeFileExists(Path.Combine(_stateDirectory, "pending-manual-task.txt")) ||
                        SafeFileExists(Path.Combine(_stateDirectory, "pending-manual-task-owner.json")))
                        throw new InvalidDataException("pending_slot_occupied");
                    if (SafeFileExists(Path.Combine(_stateDirectory, "pending-autonomous-pr.json")) ||
                        SafeFileExists(Path.Combine(_stateDirectory, "pending-state-cleanup.json")))
                        throw new InvalidDataException("pending_pr_or_cleanup");
                    if (SafeFileExists(Path.Combine(_stateDirectory, "latest-worktree-checkpoint.json")) ||
                        Directory.GetFiles(_stateDirectory, "worktree-checkpoint-*").Length > 0 ||
                        Directory.GetFiles(_stateDirectory, "worktree-plan-*").Length > 0)
                        throw new InvalidDataException("checkpoint_present_or_unknown");
                    Dictionary<string, object> manifest = ReadJsonFile(
                        Path.Combine("archived-pending-tasks", taskId, "manifest.json"), 65536
                    ) as Dictionary<string, object>;
                    if (manifest == null || Convert.ToInt32(manifest.ContainsKey("schema_version") ? manifest["schema_version"] : 0) != 1 ||
                        Convert.ToString(manifest.ContainsKey("task_id") ? manifest["task_id"] : "") != taskId ||
                        manifest.ContainsKey("restored_at_ms"))
                        throw new InvalidDataException("archive_manifest_invalid_or_already_restored");
                    byte[] task = File.ReadAllBytes(taskPath);
                    byte[] owner = File.ReadAllBytes(ownerPath);
                    if (task.Length < 1 || task.Length > 8192 || owner.Length > 65536 ||
                        Convert.ToString(manifest.ContainsKey("task_sha256") ? manifest["task_sha256"] : "") != Sha256(task) ||
                        Convert.ToString(manifest.ContainsKey("owner_sha256") ? manifest["owner_sha256"] : "") != Sha256(owner))
                        throw new InvalidDataException("archive_integrity_mismatch");
                    string archivedAt = Convert.ToString(manifest.ContainsKey("archived_at_ms") ? manifest["archived_at_ms"] : "", System.Globalization.CultureInfo.InvariantCulture);
                    string currentRevision = Sha256(Encoding.UTF8.GetBytes(
                        Sha256(task) + "|" + Sha256(owner) + "|" + archivedAt + "|"
                    ));
                    if (!String.Equals(currentRevision, revision, StringComparison.Ordinal))
                        throw new InvalidDataException("stale_revision");
                    Dictionary<string, object> ownerRecord;
                    try { ownerRecord = new JavaScriptSerializer().DeserializeObject(
                        new UTF8Encoding(false, true).GetString(owner)) as Dictionary<string, object>; }
                    catch { throw new InvalidDataException("archive_owner_invalid"); }
                    object ownerTaskHash;
                    if (ownerRecord == null || !ownerRecord.TryGetValue("task_sha256", out ownerTaskHash) ||
                        Convert.ToString(ownerTaskHash) != Sha256(CanonicalTaskBytes(task)))
                        throw new InvalidDataException("archive_owner_mismatch");
                    WriteAtomic(Path.Combine(_stateDirectory, "pending-manual-task.txt"), task);
                    WriteAtomic(Path.Combine(_stateDirectory, "pending-manual-task-owner.json"), owner);
                    manifest["restored_at_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    WriteAtomic(manifestPath, Encoding.UTF8.GetBytes(_json.Serialize(manifest) + "\n"));
                    return new Dictionary<string, object> {
                        { "task_id", taskId }, { "status", "restored" }, { "preserved", true }
                    };
                }, 250);
            }, 2000);
        }, 250);
    }

    private void HandlePendingTaskRequest(
        string method, string path, Dictionary<string, object> body,
        out int status, out object payload
    )
    {
        status = 200;
        payload = null;
        bool listPending = method == "GET" && path == "/v1/tasks/pending";
        bool listArchived = method == "GET" && path == "/v1/tasks/archived";
        bool archive = method == "POST" && path == "/v1/tasks/archive";
        bool restore = method == "POST" && path == "/v1/tasks/restore";
        if (!(listPending || listArchived || archive || restore) ||
            (listPending || listArchived) && body != null ||
            (archive || restore) && (body == null || body.Count != 2 ||
                !body.ContainsKey("task_id") || !body.ContainsKey("revision") ||
                !System.Text.RegularExpressions.Regex.IsMatch(Convert.ToString(body["task_id"]), "^[a-f0-9]{64}$") ||
                !System.Text.RegularExpressions.Regex.IsMatch(Convert.ToString(body["revision"]), "^[a-f0-9]{64}$")))
        {
            status = 400; payload = Error("invalid_task_request"); return;
        }
        try
        {
            if (listPending) payload = ListPendingTasks();
            else if (listArchived) payload = ListArchivedTasks();
            else if (archive) payload = MutatePendingTask(method, body);
            else payload = RestoreArchivedTask(body);
        }
        catch (System.TimeoutException)
        {
            status = 409;
            payload = new Dictionary<string, object> {
                { "conflict", true }, { "message", "A task lifecycle lock is held by an active writer." },
                { "blockers", new[] { "state_lock_or_writer_active" } }
            };
        }
        catch (InvalidDataException ex)
        {
            status = 409;
            payload = new Dictionary<string, object> {
                { "conflict", true }, { "message", ex.Message }, { "blockers", new[] { ex.Message } }
            };
        }
        catch (IOException)
        {
            status = 503; payload = Error("task_state_unavailable");
        }
    }

    private void LogWorkerException(Exception exception)
    {
        try
        {
            string enabled = Environment.GetEnvironmentVariable(
                "AUTONOMOUS_STATE_BROKER_DIAGNOSTICS",
                EnvironmentVariableTarget.Machine
            );
            if (!String.Equals(enabled, "1", StringComparison.Ordinal)) return;
            string path = Path.Combine(_stateDirectory, "service-errors.log");
            string entry = DateTime.UtcNow.ToString("o") + " " + exception.ToString() + Environment.NewLine;
            byte[] bytes = Encoding.UTF8.GetBytes(entry);
            if (bytes.Length > 65536)
                bytes = Encoding.UTF8.GetBytes(entry.Substring(0, 16000));
            using (FileStream file = new FileStream(path, FileMode.OpenOrCreate, FileAccess.Write, FileShare.ReadWrite))
            {
                if (file.Length + bytes.Length > 262144) file.SetLength(0);
                file.Seek(0, SeekOrigin.End);
                file.Write(bytes, 0, bytes.Length);
                file.Flush();
            }
        }
        catch { }
    }

    private static int RemainingMilliseconds(DateTime deadlineUtc, string operation)
    {
        int remaining = (int)Math.Ceiling((deadlineUtc - DateTime.UtcNow).TotalMilliseconds);
        if (remaining <= 0)
            throw new System.TimeoutException("The local state-broker timed out while " + operation + ".");
        return remaining;
    }

    private static T WaitForPipeIo<T>(Task<T> task, DateTime deadlineUtc, string operation)
    {
        if (!task.Wait(RemainingMilliseconds(deadlineUtc, operation)))
            throw new System.TimeoutException("The local state-broker timed out while " + operation + ".");
        return task.GetAwaiter().GetResult();
    }

    private static void WaitForPipeIo(Task task, DateTime deadlineUtc, string operation)
    {
        if (!task.Wait(RemainingMilliseconds(deadlineUtc, operation)))
            throw new System.TimeoutException("The local state-broker timed out while " + operation + ".");
        task.GetAwaiter().GetResult();
    }

    private static string ReadBoundedLine(Stream stream, int maximumBytes)
    {
        DateTime deadlineUtc = DateTime.UtcNow.AddMilliseconds(3000);
        using (MemoryStream buffer = new MemoryStream())
        {
            byte[] chunk = new byte[1024];
            while (buffer.Length <= maximumBytes)
            {
                int count = WaitForPipeIo(
                    stream.ReadAsync(chunk, 0, chunk.Length),
                    deadlineUtc,
                    "reading a request"
                );
                if (count <= 0) return null;
                int newline = Array.IndexOf(chunk, (byte)10, 0, count);
                int length = newline >= 0 ? newline : count;
                for (int i = 0; i < length; i++)
                {
                    if (chunk[i] != 13) buffer.WriteByte(chunk[i]);
                }
                if (buffer.Length > maximumBytes)
                    throw new InvalidDataException("The local state-broker request exceeded its size limit.");
                if (newline >= 0)
                    return Encoding.UTF8.GetString(buffer.ToArray());
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
        DateTime deadlineUtc = DateTime.UtcNow.AddMilliseconds(3000);
        WaitForPipeIo(stream.WriteAsync(bytes, 0, bytes.Length), deadlineUtc, "writing a response");
        WaitForPipeIo(stream.FlushAsync(), deadlineUtc, "flushing a response");
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

    private static bool IsSafeCheckpointRunId(string value)
    {
        if (String.IsNullOrEmpty(value) || value.Length > 100) return false;
        for (int i = 0; i < value.Length; i++)
        {
            char ch = value[i];
            if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
                  (ch >= '0' && ch <= '9') || ch == '-' || ch == '_'))
                return false;
        }
        return true;
    }

    private static bool TryReadCheckpointInteger(
        Dictionary<string, object> value, string key, out long parsed
    )
    {
        parsed = 0;
        object raw;
        if (value == null || !value.TryGetValue(key, out raw) || raw is Boolean)
            return false;
        try
        {
            parsed = Convert.ToInt64(raw);
            return true;
        }
        catch { return false; }
    }

    private static Dictionary<string, object> UnknownCheckpoint(string reason)
    {
        return new Dictionary<string, object> {
            { "available", true }, { "verified", false }, { "reason", reason }
        };
    }

    private static Dictionary<string, object> NoCheckpoint()
    {
        return new Dictionary<string, object> {
            { "available", false }, { "verified", true }
        };
    }

    private object ReadCheckpointSummary()
    {
        // The pointer is atomically replaced by the watcher. Older installations
        // without an index may resolve only the current run's fixed checkpoint name.
        const string indexName = "latest-worktree-checkpoint.json";
        Dictionary<string, object> index = null;
        string runId;
        long task;
        if (!SafeFileExists(Path.Combine(_stateDirectory, indexName)))
        {
            string currentPath = Path.Combine(_stateDirectory, "current.json");
            if (!SafeFileExists(currentPath)) return NoCheckpoint();
            Dictionary<string, object> current = ReadJsonFile("current.json", 256000) as Dictionary<string, object>;
            if (current == null) return UnknownCheckpoint("current_progress_unreadable");
            if (!current.ContainsKey("run_id")) return NoCheckpoint();
            runId = Convert.ToString(current["run_id"]);
            object currentTask;
            if (current.TryGetValue("task", out currentTask))
            {
                try { task = Convert.ToInt64(currentTask); }
                catch { return UnknownCheckpoint("current_progress_invalid"); }
            }
            else task = 1;
        }
        else
        {
            index = ReadJsonFile(indexName, 16 * 1024) as Dictionary<string, object>;
            if (index == null) return UnknownCheckpoint("checkpoint_index_unreadable");
            long indexSchema;
            object active;
            if (!TryReadCheckpointInteger(index, "schema_version", out indexSchema) || indexSchema != 1 ||
                !index.TryGetValue("active", out active) || !(active is Boolean))
                return UnknownCheckpoint("checkpoint_index_invalid");
            if (!(Boolean)active) return NoCheckpoint();
            runId = Convert.ToString(index.ContainsKey("run_id") ? index["run_id"] : "");
            if (!TryReadCheckpointInteger(index, "task", out task))
                return UnknownCheckpoint("checkpoint_index_identity_invalid");
        }

        if (!IsSafeCheckpointRunId(runId) || task < 1 || task > Int32.MaxValue)
            return UnknownCheckpoint("checkpoint_index_identity_invalid");

        string stem = "worktree-checkpoint-" + runId + "-" + task.ToString();
        string metadataName = stem + ".json";
        string patchPath = Path.Combine(_stateDirectory, stem + ".patch");
        bool metadataExists = SafeFileExists(Path.Combine(_stateDirectory, metadataName));
        bool patchExists = SafeFileExists(patchPath);
        if (index == null && !metadataExists && !patchExists) return NoCheckpoint();
        if (!metadataExists || !patchExists)
            return UnknownCheckpoint("checkpoint_files_incomplete");

        Dictionary<string, object> metadata = ReadJsonFile(metadataName, 64 * 1024) as Dictionary<string, object>;
        if (metadata == null) return UnknownCheckpoint("checkpoint_metadata_unreadable");
        long schema, savedTask, patchBytes;
        if (!TryReadCheckpointInteger(metadata, "schema_version", out schema) ||
            (schema != 2 && schema != 3 && schema != 4) ||
            !TryReadCheckpointInteger(metadata, "task", out savedTask) || savedTask != task ||
            Convert.ToString(metadata.ContainsKey("run_id") ? metadata["run_id"] : "") != runId ||
            !TryReadCheckpointInteger(metadata, "patch_bytes", out patchBytes) || patchBytes < 0)
            return UnknownCheckpoint("checkpoint_metadata_mismatch");

        string baseSha = Convert.ToString(metadata.ContainsKey("base_sha") ? metadata["base_sha"] : "");
        if (baseSha.Length != 40) return UnknownCheckpoint("checkpoint_metadata_mismatch");
        for (int i = 0; i < baseSha.Length; i++)
        {
            char ch = baseSha[i];
            if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))
                return UnknownCheckpoint("checkpoint_metadata_mismatch");
        }

        double updatedAt;
        try
        {
            updatedAt = Convert.ToDouble(metadata.ContainsKey("updated_at") ? metadata["updated_at"] : null);
        }
        catch { return UnknownCheckpoint("checkpoint_metadata_mismatch"); }
        if (Double.IsNaN(updatedAt) || Double.IsInfinity(updatedAt))
            return UnknownCheckpoint("checkpoint_metadata_mismatch");

        long actualPatchBytes;
        try { actualPatchBytes = new FileInfo(patchPath).Length; }
        catch { return UnknownCheckpoint("checkpoint_patch_unreadable"); }
        if (actualPatchBytes != patchBytes)
            return UnknownCheckpoint("checkpoint_patch_size_mismatch");

        if (index != null)
        {
            long indexBytes;
            double indexUpdatedAt;
            try { indexUpdatedAt = Convert.ToDouble(index.ContainsKey("updated_at") ? index["updated_at"] : null); }
            catch { return UnknownCheckpoint("checkpoint_index_mismatch"); }
            if (!TryReadCheckpointInteger(index, "patch_bytes", out indexBytes) ||
                indexBytes != actualPatchBytes ||
                Convert.ToString(index.ContainsKey("base_sha") ? index["base_sha"] : "") != baseSha ||
                Double.IsNaN(indexUpdatedAt) || Double.IsInfinity(indexUpdatedAt) ||
                indexUpdatedAt != updatedAt)
                return UnknownCheckpoint("checkpoint_index_mismatch");
        }

        return new Dictionary<string, object> {
            { "available", true },
            { "verified", true },
            { "run_id", runId },
            { "task", task },
            { "base_sha", baseSha },
            { "updated_at", updatedAt },
            { "patch_bytes", actualPatchBytes },
            { "plan_available", SafeFileExists(Path.Combine(_stateDirectory, "worktree-plan-" + runId + "-" + task.ToString() + ".json")) }
        };
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
            Dictionary<string, object> checkpoint = ReadCheckpointSummary() as Dictionary<string, object>;
            if (checkpoint == null)
                checkpoint = UnknownCheckpoint("checkpoint_status_unavailable");
            if (Convert.ToBoolean(checkpoint["available"]) ||
                (checkpoint.ContainsKey("verified") && !Convert.ToBoolean(checkpoint["verified"])))
                blockers.Add("checkpoint");

            string currentPath = Path.Combine(_stateDirectory, "current.json");
            if (SafeFileExists(currentPath))
            {
                Dictionary<string, object> current = ReadJsonFile("current.json", 256000) as Dictionary<string, object>;
                object rawStatus;
                string progressStatus = current != null && current.TryGetValue("status", out rawStatus)
                    ? Convert.ToString(rawStatus).Trim().ToLowerInvariant()
                    : "";
                string[] terminalProgressStates = { "completed", "succeeded", "success", "complete", "failed", "cancelled", "skipped", "blocked" };
                if (current == null || !Array.Exists(terminalProgressStates, item =>
                    String.Equals(item, progressStatus, StringComparison.Ordinal)))
                    blockers.Add("active_progress_run");
            }
            return new Dictionary<string, object> {
                { "available", true },
                { "blocked", blockers.Count > 0 },
                { "markers", markers },
                { "checkpoint", checkpoint },
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
        try
        {
            // Lock order matches the Runner: repository -> durable state -> checkpoint store.
            // The broker enforces this itself because local pipe clients can call cleanup
            // without passing through the dashboard's GitHub active-run preflight.
            return WithRepositoryExecutionLock(delegate
            {
                return WithStateLock(delegate
                {
                    try
                    {
                        return WithCheckpointStoreLock(delegate
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
                            Dictionary<string, object> checkpoint = ReadCheckpointSummary() as Dictionary<string, object>;
                            if (checkpoint == null || Convert.ToBoolean(checkpoint["available"]) ||
                                (checkpoint.ContainsKey("verified") && !Convert.ToBoolean(checkpoint["verified"])))
                                blockers.Add("latest-worktree-checkpoint");

                            // The workflow writes current.json before the run-scoped
                            // repository lock is acquired. Treat a live or unknown
                            // progress state as a blocker even when the lock is free.
                            string currentProgressPath = Path.Combine(_stateDirectory, "current.json");
                            if (SafeFileExists(currentProgressPath))
                            {
                                Dictionary<string, object> current = ReadJsonFile("current.json", 256000) as Dictionary<string, object>;
                                object rawStatus;
                                string progressStatus = current != null && current.TryGetValue("status", out rawStatus)
                                    ? Convert.ToString(rawStatus).Trim().ToLowerInvariant()
                                    : "";
                                string[] terminalProgressStates = { "completed", "succeeded", "success", "complete", "failed", "cancelled", "skipped", "blocked" };
                                if (current == null || !Array.Exists(terminalProgressStates, item =>
                                    String.Equals(item, progressStatus, StringComparison.Ordinal)))
                                    blockers.Add("active_progress_run");
                            }

                            if (blockers.Count > 0)
                                return new Dictionary<string, object> {
                                    { "conflict", true }, { "blockers", blockers.ToArray() },
                                    { "message", "Durable task/checkpoint state is pending; cleanup was refused." }
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
                            List<FileStream> cleanupHandles = new List<FileStream>();
                            List<string> cleanupPaths = new List<string>();
                            List<bool> existedBefore = new List<bool>();
                            try
                            {
                                // Block non-cooperating progress writers while the cleanup
                                // decision and deletion are in progress. Readers remain allowed.
                                foreach (string name in cleanupNames)
                                {
                                    string filePath = Path.Combine(_stateDirectory, name);
                                    bool existed = SafeFileExists(filePath);
                                    FileStream handle = new FileStream(
                                        filePath,
                                        FileMode.OpenOrCreate,
                                        FileAccess.ReadWrite,
                                        FileShare.Read | FileShare.Delete
                                    );
                                    cleanupHandles.Add(handle);
                                    cleanupPaths.Add(filePath);
                                    existedBefore.Add(existed);
                                }

                                int currentIndex = Array.IndexOf(cleanupNames, "current.json");
                                FileStream currentHandle = cleanupHandles[currentIndex];
                                if (currentHandle.Length > 0)
                                {
                                    currentHandle.Position = 0;
                                    string currentRaw;
                                    using (StreamReader reader = new StreamReader(
                                        currentHandle, Encoding.UTF8, true, 1024, true))
                                    {
                                        currentRaw = reader.ReadToEnd();
                                    }
                                    Dictionary<string, object> current =
                                        _json.DeserializeObject(currentRaw) as Dictionary<string, object>;
                                    object rawStatus;
                                    string progressStatus = current != null && current.TryGetValue("status", out rawStatus)
                                        ? Convert.ToString(rawStatus).Trim().ToLowerInvariant()
                                        : "";
                                    string[] terminalProgressStates = { "completed", "failed", "cancelled", "skipped", "blocked" };
                                    if (current == null || !Array.Exists(terminalProgressStates, item =>
                                        String.Equals(item, progressStatus, StringComparison.Ordinal)))
                                    {
                                        return new Dictionary<string, object> {
                                            { "conflict", true },
                                            { "blockers", new[] { "active_progress_run" } },
                                            { "message", "The current progress state is active or unknown; cleanup was refused." }
                                        };
                                    }
                                }
                                else if (existedBefore[currentIndex])
                                {
                                    return new Dictionary<string, object> {
                                        { "conflict", true },
                                        { "blockers", new[] { "active_progress_run" } },
                                        { "message", "The current progress file is empty or incomplete; cleanup was refused." }
                                    };
                                }

                                for (int i = 0; i < cleanupNames.Length; i++)
                                {
                                    try
                                    {
                                        long size = cleanupHandles[i].Length;
                                        if (size > 0)
                                        {
                                            File.Delete(cleanupPaths[i]);
                                            removed++;
                                            removedBytes += size;
                                        }
                                        else if (!existedBefore[i])
                                        {
                                            File.Delete(cleanupPaths[i]);
                                        }
                                    }
                                    catch (FileNotFoundException) { }
                                    catch { failed.Add(cleanupNames[i]); }
                                }
                            }
                            catch (IOException)
                            {
                                for (int i = 0; i < cleanupHandles.Count; i++)
                                {
                                    try
                                    {
                                        if (!existedBefore[i] && cleanupHandles[i].Length == 0)
                                            File.Delete(cleanupPaths[i]);
                                    }
                                    catch { }
                                }
                                return new Dictionary<string, object> {
                                    { "conflict", true },
                                    { "blockers", new[] { "progress_writer_active" } },
                                    { "message", "A progress writer is active; cleanup was refused without deleting progress data." }
                                };
                            }
                            finally
                            {
                                foreach (FileStream handle in cleanupHandles)
                                {
                                    try { handle.Dispose(); } catch { }
                                }
                            }

                            return new Dictionary<string, object> {
                                { "conflict", false },
                                { "cleaned", failed.Count == 0 },
                                { "files_removed", removed },
                                { "files_cleared", removed },
                                { "bytes_removed", removedBytes },
                                { "bytes_cleared", removedBytes },
                                { "failed_files", failed.ToArray() },
                                { "preserved", new[] { "history.jsonl", "scorecard-baseline.json", "checkpoint files", "pending task/PR markers" } }
                            };
                        }, 250);
                    }
                    catch (System.TimeoutException)
                    {
                        return new Dictionary<string, object> {
                            { "conflict", true },
                            { "blockers", new[] { "checkpoint_writer_active" } },
                            { "message", "A checkpoint watcher owns the durable checkpoint store; cleanup was refused." }
                        };
                    }
                }, 2000);
            }, 250);
        }
        catch (System.TimeoutException)
        {
            return new Dictionary<string, object> {
                { "conflict", true },
                { "blockers", new[] { "repository_execution_active" } },
                { "message", "An autonomous Runner owns the repository execution lock; cleanup was refused." }
            };
        }
    }

    private object WithRepositoryExecutionLock(Func<object> operation, int timeoutMilliseconds)
    {
        string lockPath = Path.Combine(_stateDirectory, "repository-execution.lock");
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
                    if (DateTime.UtcNow >= deadline)
                        throw new System.TimeoutException("Repository execution lock timeout.");
                    Thread.Sleep(50);
                }
            }
            try { return operation(); }
            finally { try { stream.Unlock(0, 1); } catch { } }
        }
    }

    private object WithCheckpointStoreLock(Func<object> operation, int timeoutMilliseconds)
    {
        string lockPath = Path.Combine(_stateDirectory, "autonomous-checkpoint-store.lock");
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
                    if (DateTime.UtcNow >= deadline)
                        throw new System.TimeoutException("Checkpoint store lock timeout.");
                    Thread.Sleep(50);
                }
            }
            try { return operation(); }
            finally { try { stream.Unlock(0, 1); } catch { } }
        }
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
                    if (DateTime.UtcNow >= deadline) throw new System.TimeoutException("Shared state lock timeout.");
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
