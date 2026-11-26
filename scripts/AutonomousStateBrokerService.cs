using System;
using System.Diagnostics;
using System.ComponentModel;
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
    private const string BrokerOwnerSid = "S-1-5-19";
    private const string ModifySidsMachineVariable = "AUTONOMOUS_ENGINEERING_STATE_MODIFY_SIDS";
    private const int MaxAclEntries = 100000;
    private string _stateDirectory;
    private bool _lastHealth;
    private bool _lastStorageHealth;
    private bool _lastAclHealth;
    private string _lastAclFailureReason = "not_checked";
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
        bool dispatchAdmissionEndpoint = path == "/v1/dispatch-admission/acquire" ||
            path == "/v1/dispatch-admission/complete";
        if (request.ContainsKey("body") && ((!taskEndpoint && !dispatchAdmissionEndpoint) || body == null))
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
        if (dispatchAdmissionEndpoint)
        {
            object admissionResult;
            int admissionStatus;
            HandleDispatchAdmission(method, path, body, out admissionStatus, out admissionResult);
            WriteResponse(pipe, admissionStatus, admissionResult);
            return;
        }

        if (method == "GET" && (path == "/v1/health" || path == "/v1/health/verify"))
        {
            bool forceVerification = path == "/v1/health/verify";
            bool ready = VerifyStorageAccessCached(forceVerification);
            WriteResponse(pipe, ready ? 200 : 503, new Dictionary<string, object> {
                { "ok", true },
                { "ready", ready },
                { "storage_ready", _lastStorageHealth },
                { "acl_ready", _lastAclHealth },
                { "acl_reason", _lastAclFailureReason },
                { "service", ServiceName },
                { "schema_version", 1 }
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
            try
            {
                Dictionary<string, object> cleanup = CleanupProgress() as Dictionary<string, object>;
                object conflict;
                int status = cleanup != null && cleanup.TryGetValue("conflict", out conflict) &&
                    conflict is Boolean && (Boolean)conflict ? 409 : 200;
                WriteResponse(pipe, status, cleanup ?? Error("cleanup_failed"));
            }
            catch (Exception exception)
            {
                string code = CleanupFailureCode(exception);
                LogWorkerException(exception);
                try
                {
                    EventLog.WriteEntry(
                        "PersonalAIAssistantStateBroker cleanup failed: " + code,
                        EventLogEntryType.Warning
                    );
                }
                catch { }
                WriteResponse(pipe, 503, Error(code));
            }
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


    private static bool IsPositiveInteger(object value)
    {
        if (!(value is Int32) && !(value is Int64)) return false;
        try { return Convert.ToInt64(value, System.Globalization.CultureInfo.InvariantCulture) > 0; }
        catch { return false; }
    }

    private static bool IsPositiveFiniteNumber(object value)
    {
        if (value == null || value is Boolean) return false;
        try
        {
            double number = Convert.ToDouble(value, System.Globalization.CultureInfo.InvariantCulture);
            return !Double.IsNaN(number) && !Double.IsInfinity(number) && number > 0;
        }
        catch { return false; }
    }

    private static bool IsLowerHex(string value, int length)
    {
        if (value == null || value.Length != length) return false;
        foreach (char ch in value)
            if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return false;
        return true;
    }

    private Dictionary<string, object> ParseAndValidateTaskOwner(byte[] taskBytes, byte[] ownerBytes)
    {
        Dictionary<string, object> owner;
        try
        {
            owner = new JavaScriptSerializer { MaxJsonLength = 65536 }.DeserializeObject(
                new UTF8Encoding(false, true).GetString(ownerBytes)
            ) as Dictionary<string, object>;
            // Validate UTF-8 task bytes before hashing or exposing metadata.
            new UTF8Encoding(false, true).GetString(taskBytes);
        }
        catch { throw new InvalidDataException("Pending task owner metadata is unreadable."); }
        object schema, taskHash, ownerRun, submittedRun, source, ownerAttempt, submittedAttempt,
            createdAt, claimedAt, claimCount;
        if (owner == null ||
            !owner.TryGetValue("schema_version", out schema) || schema is Boolean ||
            (!(schema is Int32) && !(schema is Int64)) || Convert.ToInt32(schema) != 1 ||
            !owner.TryGetValue("task_sha256", out taskHash) || !(taskHash is string) ||
            !IsLowerHex((string)taskHash, 64) ||
            !String.Equals((string)taskHash, Sha256(CanonicalTaskBytes(taskBytes)), StringComparison.Ordinal) ||
            !owner.TryGetValue("owner_run_id", out ownerRun) || !(ownerRun is string) || String.IsNullOrWhiteSpace((string)ownerRun) ||
            !owner.TryGetValue("submitted_run_id", out submittedRun) || !(submittedRun is string) || String.IsNullOrWhiteSpace((string)submittedRun) ||
            !owner.TryGetValue("source", out source) || !(source is string) ||
            !new[] { "manual_dispatch", "recovery", "legacy_migration" }.Contains((string)source) ||
            !owner.TryGetValue("owner_attempt", out ownerAttempt) || !IsPositiveInteger(ownerAttempt) ||
            !owner.TryGetValue("submitted_attempt", out submittedAttempt) || !IsPositiveInteger(submittedAttempt) ||
            !owner.TryGetValue("claim_count", out claimCount) || !IsPositiveInteger(claimCount) ||
            !owner.TryGetValue("created_at", out createdAt) || !IsPositiveFiniteNumber(createdAt) ||
            !owner.TryGetValue("claimed_at", out claimedAt) || !IsPositiveFiniteNumber(claimedAt))
            throw new InvalidDataException("Pending task owner metadata does not match schema v1.");
        foreach (string key in new[] { "correlation_id", "submitted_correlation_id" })
        {
            object value;
            if (owner.TryGetValue(key, out value) &&
                (!(value is string) || !IsLowerHex((string)value, 32)))
                throw new InvalidDataException("Pending task owner correlation metadata is invalid.");
        }
        return owner;
    }

    private Dictionary<string, object> ReadPendingTaskRecord()
    {
        string taskPath = Path.Combine(_stateDirectory, "pending-manual-task.txt");
        string ownerPath = Path.Combine(_stateDirectory, "pending-manual-task-owner.json");
        if (!SafeFileExists(taskPath))
        {
            if (SafeFileExists(ownerPath))
                throw new InvalidDataException("owner_metadata_without_task");
            return null;
        }
        FileInfo taskInfo = new FileInfo(taskPath);
        if (taskInfo.Length < 1 || taskInfo.Length > 8192)
            throw new InvalidDataException("Pending task size is invalid.");
        byte[] taskBytes = File.ReadAllBytes(taskPath);
        byte[] ownerBytes;
        try { ownerBytes = File.ReadAllBytes(ownerPath); }
        catch { throw new InvalidDataException("Pending task owner metadata is missing or unreadable."); }
        if (ownerBytes.Length > 65536)
            throw new InvalidDataException("Pending task owner metadata is oversized.");
        Dictionary<string, object> owner = ParseAndValidateTaskOwner(taskBytes, ownerBytes);
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

    private bool HasStateArtifacts(string searchPattern)
    {
        // Only fixed, broker-owned patterns are accepted here; no request value
        // is ever used as a filesystem path or search pattern.
        foreach (FileSystemInfo entry in new DirectoryInfo(_stateDirectory).EnumerateFileSystemInfos(searchPattern))
            return true;
        return false;
    }

    private List<string> PendingTaskBlockers()
    {
        List<string> blockers = new List<string>();
        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-autonomous-pr.json")))
            blockers.Add("pending_pr");
        if (SafeFileExists(Path.Combine(_stateDirectory, "pending-state-cleanup.json")))
            blockers.Add("cleanup_in_progress");
        if (SafeFileExists(Path.Combine(_stateDirectory, "latest-worktree-checkpoint.json")) ||
            HasStateArtifacts("worktree-checkpoint-*") ||
            HasStateArtifacts("worktree-plan-*"))
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

    private string EnsureTaskArchiveRoot()
    {
        string root = Path.Combine(_stateDirectory, "archived-pending-tasks");
        Directory.CreateDirectory(root);
        DirectoryInfo info = new DirectoryInfo(root);
        if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("Task archive root cannot be a reparse point.");
        return root;
    }

    private object ListArchivedTasks()
    {
        return WithStateLock(delegate
        {
            string root = EnsureTaskArchiveRoot();
            List<object> tasks = new List<object>();
            foreach (FileSystemInfo entry in new DirectoryInfo(root).EnumerateFileSystemInfos())
            {
                DirectoryInfo packageInfo = entry as DirectoryInfo;
                if (packageInfo == null)
                    throw new InvalidDataException("Archive contains an unknown entry.");
                string directory = packageInfo.FullName;
                string id = packageInfo.Name;
                if ((packageInfo.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Archive package cannot be a reparse point.");
                if (!System.Text.RegularExpressions.Regex.IsMatch(id, "^[a-f0-9]{64}$"))
                    throw new InvalidDataException("Archive contains an unknown entry.");
                string taskPath = Path.Combine(directory, "task.txt");
                string ownerPath = Path.Combine(directory, "owner.json");
                string manifestPath = Path.Combine(directory, "manifest.json");
                if (!SafeFileExists(taskPath) || !SafeFileExists(ownerPath) || !SafeFileExists(manifestPath))
                    throw new InvalidDataException("Archived task package is incomplete.");
                byte[] task = File.ReadAllBytes(taskPath), owner = File.ReadAllBytes(ownerPath);
                Dictionary<string, object> manifest = ReadJsonFile(Path.Combine("archived-pending-tasks", id, "manifest.json"), 65536) as Dictionary<string, object>;
                if (manifest == null ||
                    Convert.ToInt32(manifest.ContainsKey("schema_version") ? manifest["schema_version"] : 0) != 1 ||
                    Convert.ToString(manifest.ContainsKey("task_id") ? manifest["task_id"] : "") != id ||
                    Convert.ToString(manifest.ContainsKey("task_sha256") ? manifest["task_sha256"] : "") != Sha256(task) ||
                    Convert.ToString(manifest.ContainsKey("owner_sha256") ? manifest["owner_sha256"] : "") != Sha256(owner) ||
                    !IsLowerHex(Convert.ToString(manifest.ContainsKey("source_revision") ? manifest["source_revision"] : ""), 64))
                    throw new InvalidDataException("Archived task manifest is inconsistent.");
                string archivedAt = Convert.ToString(manifest.ContainsKey("archived_at_ms") ? manifest["archived_at_ms"] : "", System.Globalization.CultureInfo.InvariantCulture);
                string restoredAt = Convert.ToString(manifest.ContainsKey("restored_at_ms") ? manifest["restored_at_ms"] : "", System.Globalization.CultureInfo.InvariantCulture);
                string revision = Sha256(Encoding.UTF8.GetBytes(
                    Sha256(task) + "|" + Sha256(owner) + "|" + archivedAt + "|" + restoredAt
                ));
                Dictionary<string, object> ownerObject = ParseAndValidateTaskOwner(task, owner);
                tasks.Add(new Dictionary<string, object> {
                    { "task_id", id }, { "revision", revision },
                    { "status", manifest.ContainsKey("restored_at_ms") ? "restored" : "archived" },
                    { "created_at", ownerObject != null && ownerObject.ContainsKey("created_at") ? ownerObject["created_at"] : null },
                    { "source", ownerObject["source"] },
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
                            HasStateArtifacts("worktree-checkpoint-*") ||
                            HasStateArtifacts("worktree-plan-*"))
                            throw new InvalidDataException("checkpoint_present_or_unknown");
                        List<string> activeStateBlockers = PendingTaskBlockers();
                        if (activeStateBlockers.Count > 0)
                            throw new InvalidDataException(String.Join(",", activeStateBlockers.ToArray()));
                        string recoveryRoot = EnsureTaskArchiveRoot();
                        string recoveryPackage = Path.Combine(recoveryRoot, taskId);
                        if (Directory.Exists(recoveryPackage))
                        {
                            if ((new DirectoryInfo(recoveryPackage).Attributes & FileAttributes.ReparsePoint) != 0)
                                throw new InvalidDataException("Archive package cannot be a reparse point.");
                            string savedTaskPath = Path.Combine(recoveryPackage, "task.txt");
                            string savedOwnerPath = Path.Combine(recoveryPackage, "owner.json");
                            Dictionary<string, object> savedManifest = ReadJsonFile(
                                Path.Combine("archived-pending-tasks", taskId, "manifest.json"), 65536
                            ) as Dictionary<string, object>;
                            if (!SafeFileExists(savedTaskPath) || !SafeFileExists(savedOwnerPath) ||
                                savedManifest == null ||
                                Convert.ToInt32(savedManifest.ContainsKey("schema_version") ? savedManifest["schema_version"] : 0) != 1 ||
                                Convert.ToString(savedManifest.ContainsKey("task_id") ? savedManifest["task_id"] : "") != taskId ||
                                Convert.ToString(savedManifest.ContainsKey("source_revision") ? savedManifest["source_revision"] : "") != revision)
                                throw new InvalidDataException("archive_retry_revision_or_package_invalid");
                            byte[] savedTask = File.ReadAllBytes(savedTaskPath);
                            byte[] savedOwner = File.ReadAllBytes(savedOwnerPath);
                            if (savedTask.Length < 1 || savedTask.Length > 8192 || savedOwner.Length > 65536 ||
                                Convert.ToString(savedManifest.ContainsKey("task_sha256") ? savedManifest["task_sha256"] : "") != Sha256(savedTask) ||
                                Convert.ToString(savedManifest.ContainsKey("owner_sha256") ? savedManifest["owner_sha256"] : "") != Sha256(savedOwner))
                                throw new InvalidDataException("archive_retry_integrity_mismatch");
                            ParseAndValidateTaskOwner(savedTask, savedOwner);
                            string recoveryActiveTaskPath = Path.Combine(_stateDirectory, "pending-manual-task.txt");
                            string recoveryActiveOwnerPath = Path.Combine(_stateDirectory, "pending-manual-task-owner.json");
                            if (SafeFileExists(recoveryActiveTaskPath) && !File.ReadAllBytes(recoveryActiveTaskPath).SequenceEqual(savedTask))
                                throw new InvalidDataException("archive_recovery_task_mismatch");
                            if (SafeFileExists(recoveryActiveOwnerPath) && !File.ReadAllBytes(recoveryActiveOwnerPath).SequenceEqual(savedOwner))
                                throw new InvalidDataException("archive_recovery_owner_mismatch");
                            if (SafeFileExists(recoveryActiveTaskPath)) File.Delete(recoveryActiveTaskPath);
                            if (SafeFileExists(recoveryActiveOwnerPath)) File.Delete(recoveryActiveOwnerPath);
                            return new Dictionary<string, object> {
                                { "task_id", taskId }, { "status", "archived" }, { "preserved", true }
                            };
                        }
                        Dictionary<string, object> record = ReadPendingTaskRecord();
                        if (record == null || Convert.ToString(record["task_id"]) != taskId)
                            throw new InvalidDataException("stale_task");
                        if (Convert.ToString(record["revision"]) != revision)
                            throw new InvalidDataException("stale_revision");
                        List<string> blockers = (List<string>)record["blockers"];
                        if (blockers.Count > 0) throw new InvalidDataException(String.Join(",", blockers.ToArray()));
                        string root = EnsureTaskArchiveRoot();
                        string final = Path.Combine(root, taskId);
                        byte[] taskBytes = (byte[])record["task_bytes"];
                        byte[] ownerBytes = (byte[])record["owner_bytes"];
                        if (Directory.Exists(final))
                        {
                            if ((new DirectoryInfo(final).Attributes & FileAttributes.ReparsePoint) != 0)
                                throw new InvalidDataException("Archive package cannot be a reparse point.");
                            string savedTaskPath = Path.Combine(final, "task.txt");
                            string savedOwnerPath = Path.Combine(final, "owner.json");
                            Dictionary<string, object> savedManifest = ReadJsonFile(
                                Path.Combine("archived-pending-tasks", taskId, "manifest.json"), 65536
                            ) as Dictionary<string, object>;
                            if (!SafeFileExists(savedTaskPath) || !SafeFileExists(savedOwnerPath) ||
                                savedManifest == null ||
                                Convert.ToString(savedManifest.ContainsKey("source_revision") ? savedManifest["source_revision"] : "") != revision ||
                                !File.ReadAllBytes(savedTaskPath).SequenceEqual(taskBytes) ||
                                !File.ReadAllBytes(savedOwnerPath).SequenceEqual(ownerBytes))
                                throw new InvalidDataException("archive_collision_or_stale_revision");
                        }
                        else
                        {
                            string stage = Path.Combine(root, ".stage-" + Guid.NewGuid().ToString("N"));
                            Directory.CreateDirectory(stage);
                            try
                            {
                                WriteAtomic(Path.Combine(stage, "task.txt"), taskBytes);
                                WriteAtomic(Path.Combine(stage, "owner.json"), ownerBytes);
                                Dictionary<string, object> manifest = new Dictionary<string, object> {
                                    { "schema_version", 1 }, { "task_id", taskId }, { "source_revision", revision },
                                    { "archived_at_ms", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() },
                                    { "task_sha256", Sha256(taskBytes) }, { "owner_sha256", Sha256(ownerBytes) }
                                };
                                WriteAtomic(Path.Combine(stage, "manifest.json"), Encoding.UTF8.GetBytes(_json.Serialize(manifest) + "\n"));
                                Directory.Move(stage, final);
                            }
                            finally { try { if (Directory.Exists(stage)) Directory.Delete(stage, true); } catch { } }
                        }
                        string finalActiveTaskPath = Path.Combine(_stateDirectory, "pending-manual-task.txt");
                        string finalActiveOwnerPath = Path.Combine(_stateDirectory, "pending-manual-task-owner.json");
                        if (SafeFileExists(finalActiveTaskPath) && !File.ReadAllBytes(finalActiveTaskPath).SequenceEqual(taskBytes))
                            throw new InvalidDataException("archive_recovery_task_mismatch");
                        if (SafeFileExists(finalActiveOwnerPath) && !File.ReadAllBytes(finalActiveOwnerPath).SequenceEqual(ownerBytes))
                            throw new InvalidDataException("archive_recovery_owner_mismatch");
                        if (SafeFileExists(finalActiveTaskPath)) File.Delete(finalActiveTaskPath);
                        if (SafeFileExists(finalActiveOwnerPath)) File.Delete(finalActiveOwnerPath);
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
                    string root = EnsureTaskArchiveRoot();
                    string package = Path.Combine(root, taskId);
                    if (Directory.Exists(package) &&
                        (new DirectoryInfo(package).Attributes & FileAttributes.ReparsePoint) != 0)
                        throw new InvalidDataException("Archive package cannot be a reparse point.");
                    string taskPath = Path.Combine(package, "task.txt");
                    string ownerPath = Path.Combine(package, "owner.json");
                    string manifestPath = Path.Combine(package, "manifest.json");
                    if (!Directory.Exists(package) || !SafeFileExists(taskPath) ||
                        !SafeFileExists(ownerPath) || !SafeFileExists(manifestPath))
                        throw new InvalidDataException("stale_task");
                    if (SafeFileExists(Path.Combine(_stateDirectory, "pending-autonomous-pr.json")) ||
                        SafeFileExists(Path.Combine(_stateDirectory, "pending-state-cleanup.json")))
                        throw new InvalidDataException("pending_pr_or_cleanup");
                    List<string> restoreBlockers = PendingTaskBlockers();
                    if (restoreBlockers.Count > 0)
                        throw new InvalidDataException(String.Join(",", restoreBlockers.ToArray()));
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
                    ParseAndValidateTaskOwner(task, owner);
                    string activeTaskPath = Path.Combine(_stateDirectory, "pending-manual-task.txt");
                    string activeOwnerPath = Path.Combine(_stateDirectory, "pending-manual-task-owner.json");
                    if (SafeFileExists(activeTaskPath) && !File.ReadAllBytes(activeTaskPath).SequenceEqual(task))
                        throw new InvalidDataException("pending_slot_occupied");
                    if (SafeFileExists(activeOwnerPath) && !File.ReadAllBytes(activeOwnerPath).SequenceEqual(owner))
                        throw new InvalidDataException("pending_owner_slot_occupied");
                    if (!SafeFileExists(activeTaskPath)) WriteAtomic(activeTaskPath, task);
                    if (!SafeFileExists(activeOwnerPath)) WriteAtomic(activeOwnerPath, owner);
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
        catch (Exception)
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

    private bool VerifyStorageAccessCached(bool forceVerification)
    {
        lock (_healthGate)
        {
            if (!forceVerification && DateTime.UtcNow - _lastHealthAt < TimeSpan.FromSeconds(15))
                return _lastHealth;

            _lastStorageHealth = VerifyStorageAccess();
            _lastAclHealth = _lastStorageHealth && VerifyStateAclIntegrity();
            _lastHealth = _lastStorageHealth && _lastAclHealth;
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

    private HashSet<string> GetExpectedStateModifySids()
    {
        HashSet<string> expected = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
            BrokerOwnerSid
        };
        string configured = Environment.GetEnvironmentVariable(
            ModifySidsMachineVariable,
            EnvironmentVariableTarget.Machine
        );
        if (String.IsNullOrWhiteSpace(configured))
        {
            _lastAclFailureReason = "expected_sid_manifest_missing";
            return null;
        }

        string[] entries = configured.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
        if (entries.Length < 1 || entries.Length > 64)
        {
            _lastAclFailureReason = "expected_sid_manifest_invalid";
            return null;
        }
        foreach (string entry in entries)
        {
            try
            {
                SecurityIdentifier sid = new SecurityIdentifier(entry.Trim());
                expected.Add(sid.Value);
            }
            catch
            {
                _lastAclFailureReason = "expected_sid_manifest_invalid";
                return null;
            }
        }
        return expected;
    }

    private bool HasExpectedStateAcl(string path, bool isDirectory, HashSet<string> expected, bool isRoot)
    {
        try
        {
            FileSystemSecurity security = isDirectory
                ? (FileSystemSecurity)Directory.GetAccessControl(
                    path, AccessControlSections.Owner | AccessControlSections.Access
                )
                : File.GetAccessControl(path, AccessControlSections.Owner | AccessControlSections.Access);

            SecurityIdentifier owner = security.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
            if (owner == null || !expected.Contains(owner.Value))
            {
                _lastAclFailureReason = "state_entry_owner_unexpected";
                return false;
            }
            if (isRoot)
            {
                if (!String.Equals(owner.Value, BrokerOwnerSid, StringComparison.OrdinalIgnoreCase))
                {
                    _lastAclFailureReason = "state_root_owner_not_local_service";
                    return false;
                }
                if (!security.AreAccessRulesProtected)
                {
                    _lastAclFailureReason = "state_root_acl_inheritance_enabled";
                    return false;
                }
            }

            AuthorizationRuleCollection rules = security.GetAccessRules(
                true, true, typeof(SecurityIdentifier)
            );
            if (rules.Count != expected.Count)
            {
                _lastAclFailureReason = "state_entry_ace_count_mismatch";
                return false;
            }

            HashSet<string> observed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (AuthorizationRule candidate in rules)
            {
                FileSystemAccessRule rule = candidate as FileSystemAccessRule;
                // NTFS commonly returns the Synchronize standard right
                // together with Modify, even when the ACE was created with
                // FileSystemRights.Modify. Accept only that documented extra bit;
                // any other additional permission remains a readiness failure.
                FileSystemRights actualRights = rule == null
                    ? 0
                    : rule.FileSystemRights;
                FileSystemRights allowedRights = FileSystemRights.Modify | FileSystemRights.Synchronize;
                if (rule == null || rule.AccessControlType != AccessControlType.Allow ||
                    (actualRights & FileSystemRights.Modify) != FileSystemRights.Modify ||
                    (actualRights & ~allowedRights) != 0)
                {
                    _lastAclFailureReason = "state_entry_ace_rights_or_type_mismatch";
                    return false;
                }
                SecurityIdentifier sid = rule.IdentityReference as SecurityIdentifier;
                if (sid == null)
                {
                    _lastAclFailureReason = "state_entry_ace_identity_unreadable";
                    return false;
                }
                if (!expected.Contains(sid.Value) || !observed.Add(sid.Value))
                {
                    _lastAclFailureReason = "state_entry_ace_identity_unexpected_or_duplicate";
                    return false;
                }
            }
            if (!observed.SetEquals(expected))
            {
                _lastAclFailureReason = "state_entry_expected_identity_missing";
                return false;
            }
            return true;
        }
        catch
        {
            _lastAclFailureReason = "state_entry_acl_unreadable";
            return false;
        }
    }

    private bool VerifyStateAclIntegrity()
    {
        _lastAclFailureReason = "none";
        try
        {
            HashSet<string> expected = GetExpectedStateModifySids();
            if (expected == null)
                return false;
            if (!Directory.Exists(_stateDirectory))
            {
                _lastAclFailureReason = "state_root_missing";
                return false;
            }

            Stack<string> pending = new Stack<string>();
            pending.Push(_stateDirectory);
            int checkedEntries = 0;
            while (pending.Count > 0)
            {
                string directory = pending.Pop();
                DirectoryInfo info = new DirectoryInfo(directory);
                if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
                {
                    _lastAclFailureReason = "state_tree_reparse_point";
                    return false;
                }
                bool isRoot = String.Equals(
                    directory, _stateDirectory, StringComparison.OrdinalIgnoreCase
                );
                if (!HasExpectedStateAcl(directory, true, expected, isRoot))
                    return false;

                foreach (string entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    checkedEntries++;
                    if (checkedEntries > MaxAclEntries)
                    {
                        _lastAclFailureReason = "state_tree_entry_limit_exceeded";
                        return false;
                    }
                    FileAttributes attributes = File.GetAttributes(entry);
                    if ((attributes & FileAttributes.ReparsePoint) != 0)
                    {
                        _lastAclFailureReason = "state_tree_reparse_point";
                        return false;
                    }
                    if ((attributes & FileAttributes.Directory) != 0)
                    {
                        pending.Push(entry);
                    }
                    else if (!HasExpectedStateAcl(entry, false, expected, false))
                    {
                        return false;
                    }
                }
            }
            return true;
        }
        catch
        {
            _lastAclFailureReason = "state_acl_scan_failed";
            return false;
        }
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

    private static bool TryFiniteNumber(object value, out double number)
    {
        number = 0;
        if (value == null || value is Boolean) return false;
        try { number = Convert.ToDouble(value, System.Globalization.CultureInfo.InvariantCulture); }
        catch { return false; }
        return !Double.IsNaN(number) && !Double.IsInfinity(number) && number >= 0;
    }

    private static double UtcUnixSeconds()
    {
        return (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
    }

    private void HandleDispatchAdmission(
        string method, string path, Dictionary<string, object> body,
        out int status, out object result)
    {
        status = 400;
        result = Error("invalid_dispatch_admission_request");
        if (!String.Equals(method, "POST", StringComparison.OrdinalIgnoreCase) || body == null) return;
        string[] expected = path == "/v1/dispatch-admission/acquire"
            ? new[] { "request_id" }
            : path == "/v1/dispatch-admission/complete"
                ? new[] { "request_id", "accepted" }
                : new string[0];
        if (expected.Length == 0 || body.Count != expected.Length || expected.Any(key => !body.ContainsKey(key))) return;
        string requestId = body["request_id"] as string;
        if (String.IsNullOrEmpty(requestId) || requestId.Length != 32 ||
            requestId.Any(ch => !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')))) return;
        bool accepted = false;
        if (path.EndsWith("/complete", StringComparison.Ordinal))
        {
            if (!(body["accepted"] is Boolean)) return;
            accepted = (Boolean)body["accepted"];
        }

        int responseStatus = 200;
        try
        {
            result = WithStateLock(delegate
            {
                string statePath = Path.Combine(_stateDirectory, "workflow-dispatch-admission.json");
                Dictionary<string, object> state = null;
                if (SafeFileExists(statePath))
                {
                    state = ReadJsonFile("workflow-dispatch-admission.json", 4096) as Dictionary<string, object>;
                    if (state == null) return new Dictionary<string, object> { { "error", "dispatch_admission_state_invalid" } };
                }
                if (state == null)
                    state = new Dictionary<string, object> {
                        { "schema_version", 1 }, { "last_accepted_at", 0.0 }, { "pending", null }
                    };
                object rawSchema, rawLast, rawPending;
                double lastAccepted;
                if (state.Count != 3 || !state.TryGetValue("schema_version", out rawSchema) || Convert.ToInt32(rawSchema) != 1 ||
                    !state.TryGetValue("last_accepted_at", out rawLast) || !TryFiniteNumber(rawLast, out lastAccepted) ||
                    !state.TryGetValue("pending", out rawPending))
                    return new Dictionary<string, object> { { "error", "dispatch_admission_state_invalid" } };
                Dictionary<string, object> pending = rawPending as Dictionary<string, object>;
                if (rawPending != null && pending == null)
                    return new Dictionary<string, object> { { "error", "dispatch_admission_state_invalid" } };

                double now = UtcUnixSeconds();
                if (path.EndsWith("/acquire", StringComparison.Ordinal))
                {
                    if (pending != null)
                    {
                        double createdAt, expiresAt;
                        object rawCreated, rawExpires, rawId;
                        if (pending.Count != 3 ||
                            !pending.TryGetValue("request_id", out rawId) ||
                            !pending.TryGetValue("created_at", out rawCreated) || !TryFiniteNumber(rawCreated, out createdAt) ||
                            !pending.TryGetValue("lease_expires_at", out rawExpires) || !TryFiniteNumber(rawExpires, out expiresAt) ||
                            !(rawId is string) || ((string)rawId).Length != 32 ||
                            ((string)rawId).Any(ch => !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) ||
                            expiresAt <= createdAt)
                            return new Dictionary<string, object> { { "error", "dispatch_admission_state_invalid" } };
                        if (now < expiresAt)
                        {
                            responseStatus = 409;
                            return new Dictionary<string, object> {
                                { "conflict", true }, { "message", "dispatch_admission_conflict" },
                                { "blockers", new[] { "dispatch_reservation_active" } }
                            };
                        }
                        // An expired caller may have dispatched before it died.
                        // Start a fresh grace interval from lease recovery.
                        lastAccepted = Math.Max(lastAccepted, now);
                        pending = null;
                        WriteAtomic(statePath, Encoding.UTF8.GetBytes(_json.Serialize(new Dictionary<string, object> {
                            { "schema_version", 1 }, { "last_accepted_at", lastAccepted }, { "pending", null }
                        })));
                    }
                    if (lastAccepted > 0 && (now < lastAccepted || now - lastAccepted < 30.0))
                    {
                        responseStatus = 409;
                        return new Dictionary<string, object> {
                            { "conflict", true }, { "message", "dispatch_admission_conflict" },
                            { "blockers", new[] { "dispatch_recently_accepted" } }
                        };
                    }
                    Dictionary<string, object> next = new Dictionary<string, object> {
                        { "schema_version", 1 },
                        { "last_accepted_at", lastAccepted },
                        { "pending", new Dictionary<string, object> {
                            { "request_id", requestId }, { "created_at", now }, { "lease_expires_at", now + 120.0 }
                        } }
                    };
                    WriteAtomic(statePath, Encoding.UTF8.GetBytes(_json.Serialize(next)));
                    responseStatus = 200;
                    return new Dictionary<string, object> { { "acquired", true }, { "last_accepted_at", lastAccepted }, { "lease_seconds", 120 } };
                }

                if (pending == null || !String.Equals(Convert.ToString(pending.ContainsKey("request_id") ? pending["request_id"] : null), requestId, StringComparison.Ordinal))
                {
                    responseStatus = 409;
                    return new Dictionary<string, object> {
                        { "conflict", true }, { "message", "dispatch_admission_lease_not_owned" },
                        { "blockers", new[] { "dispatch_admission_lease_not_owned" } }
                    };
                }
                if (accepted) lastAccepted = now;
                Dictionary<string, object> completed = new Dictionary<string, object> {
                    { "schema_version", 1 }, { "last_accepted_at", lastAccepted }, { "pending", null }
                };
                WriteAtomic(statePath, Encoding.UTF8.GetBytes(_json.Serialize(completed)));
                responseStatus = 200;
                return new Dictionary<string, object> { { "completed", true }, { "last_accepted_at", lastAccepted } };
            }, 2000);
            status = responseStatus;
            if (result is Dictionary<string, object>)
            {
                Dictionary<string, object> response = (Dictionary<string, object>)result;
                if (response.ContainsKey("error"))
                {
                    status = 503;
                }
                else if (response.ContainsKey("conflict") && Convert.ToBoolean(response["conflict"]))
                {
                    status = 409;
                }
            }
        }
        catch (System.TimeoutException)
        {
            status = 409;
            result = new Dictionary<string, object> {
                { "conflict", true }, { "message", "dispatch_admission_state_busy" },
                { "blockers", new[] { "dispatch_admission_state_busy" } }
            };
        }
        catch (Exception)
        {
            status = 503;
            result = Error("dispatch_admission_state_unavailable");
        }
    }

    private static string CleanupFailureCode(Exception exception)
    {
        if (exception is UnauthorizedAccessException ||
            exception is System.Security.SecurityException)
            return "cleanup_access_denied";
        if (exception is DirectoryNotFoundException)
            return "state_directory_missing";
        if (exception is InvalidDataException)
            return "durable_state_unverified";
        if (exception is System.TimeoutException)
            return "cleanup_lock_timeout";
        if (exception is IOException)
            return "cleanup_io_failed";
        return "cleanup_failed";
    }

    private Dictionary<string, object> ReadRepositoryExecutionOwner()
    {
        string lockPath = Path.Combine(_stateDirectory, "repository-execution.lock");
        if (!SafeFileExists(lockPath)) return null;
        try
        {
            using (FileStream stream = new FileStream(
                lockPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                // Byte zero is held by this Broker while cleanup runs. Windows
                // byte-range locking permits reading the owner record after byte zero.
                if (stream.Length <= 1) return null;
                stream.Position = 1;
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8, true, 1024, true))
                {
                    return _json.DeserializeObject(reader.ReadToEnd()) as Dictionary<string, object>;
                }
            }
        }
        catch { return null; }
    }

    private string InspectRepositoryOwnerProcess(Dictionary<string, object> owner)
    {
        if (owner == null) return "unknown";
        int pid;
        try { pid = Convert.ToInt32(owner["pid"], System.Globalization.CultureInfo.InvariantCulture); }
        catch { return "unknown"; }
        if (pid <= 0) return "unknown";

        Process process = null;
        try
        {
            process = Process.GetProcessById(pid);
            if (process.HasExited) return "missing";
            object rawStartedAt;
            if (!owner.TryGetValue("process_started_at", out rawStartedAt))
                return "unknown"; // Legacy PID-only metadata cannot rule out PID reuse.
            double recordedStartedAt = Convert.ToDouble(
                rawStartedAt, System.Globalization.CultureInfo.InvariantCulture);
            double actualStartedAt = (process.StartTime.ToUniversalTime() -
                new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            if (Double.IsNaN(recordedStartedAt) || Double.IsInfinity(recordedStartedAt) ||
                recordedStartedAt <= 0 || Double.IsNaN(actualStartedAt) || Double.IsInfinity(actualStartedAt))
                return "unknown";
            return Math.Abs(actualStartedAt - recordedStartedAt) <= 0.02 ? "alive" : "reused";
        }
        catch (ArgumentException) { return "missing"; }
        catch (InvalidOperationException) { return "missing"; }
        catch (Win32Exception) { return "unknown"; }
        catch (NotSupportedException) { return "unknown"; }
        catch (Exception) { return "unknown"; }
        finally { if (process != null) process.Dispose(); }
    }

    private bool RecoverStaleProgress(Dictionary<string, object> current, out string recoveredRunId)
    {
        recoveredRunId = null;
        if (current == null) return false;
        object rawStatus;
        string status = current.TryGetValue("status", out rawStatus)
            ? Convert.ToString(rawStatus).Trim().ToLowerInvariant() : "";
        string[] terminal = {
            "completed", "succeeded", "success", "complete", "failed",
            "cancelled", "skipped", "blocked", "interrupted"
        };
        if (Array.Exists(terminal, item => String.Equals(item, status, StringComparison.Ordinal)))
            return true;

        string runId = Convert.ToString(current.ContainsKey("run_id") ? current["run_id"] : "");
        string attempt = Convert.ToString(current.ContainsKey("run_attempt") ? current["run_attempt"] : "1");
        if (String.IsNullOrWhiteSpace(runId) || String.IsNullOrWhiteSpace(attempt))
            return false;

        Dictionary<string, object> owner = ReadRepositoryExecutionOwner();
        if (owner == null ||
            !String.Equals(Convert.ToString(owner.ContainsKey("run_id") ? owner["run_id"] : ""), runId, StringComparison.Ordinal) ||
            !String.Equals(Convert.ToString(owner.ContainsKey("run_attempt") ? owner["run_attempt"] : ""), attempt, StringComparison.Ordinal) ||
            !String.Equals(Convert.ToString(owner.ContainsKey("host") ? owner["host"] : ""), Environment.MachineName, StringComparison.OrdinalIgnoreCase))
            return false;

        string processState = InspectRepositoryOwnerProcess(owner);
        if (processState != "missing" && processState != "reused")
            return false;

        double processStartedAt = 0;
        try
        {
            if (owner.ContainsKey("process_started_at"))
                processStartedAt = Convert.ToDouble(owner["process_started_at"], System.Globalization.CultureInfo.InvariantCulture);
        }
        catch { return false; }
        string eventId = Sha256(Encoding.UTF8.GetBytes(
            runId + "|" + attempt + "|" + Convert.ToString(owner["pid"]) + "|" +
            processStartedAt.ToString("R", System.Globalization.CultureInfo.InvariantCulture)
        ));
        Dictionary<string, object> recoveryEvent = new Dictionary<string, object> {
            { "type", "progress_recovery" }, { "event_id", eventId },
            { "run_id", runId }, { "run_attempt", attempt },
            { "status", "interrupted" }, { "reason", "owner_process_exited" },
            { "recorded_at", DateTimeOffset.UtcNow.ToString("o") }
        };
        string eventsPath = Path.Combine(_stateDirectory, "events.jsonl");
        try
        {
            bool eventAlreadyRecorded = false;
            if (SafeFileExists(eventsPath))
            {
                FileInfo eventsInfo = new FileInfo(eventsPath);
                if (eventsInfo.Length <= 4 * 1024 * 1024)
                {
                    string existingEvents = File.ReadAllText(eventsPath, Encoding.UTF8);
                    eventAlreadyRecorded = existingEvents.IndexOf(eventId, StringComparison.Ordinal) >= 0;
                }
            }
            if (!eventAlreadyRecorded)
            {
                byte[] eventBytes = Encoding.UTF8.GetBytes(_json.Serialize(recoveryEvent) + "\n");
                using (FileStream stream = new FileStream(eventsPath, FileMode.Append, FileAccess.Write, FileShare.Read))
                {
                    stream.Write(eventBytes, 0, eventBytes.Length);
                    stream.Flush(true);
                }
            }
            current["status"] = "interrupted";
            current["phase"] = "recovery";
            current["phase_label"] = "اجرای قبلی متوقف شده";
            current["activity"] = "اجرای قبلی متوقف شده بود؛ وضعیت آن بازیابی شد";
            current["recovery_reason"] = "owner_process_exited";
            current["recovery_event_id"] = eventId;
            current["updated_at"] = DateTimeOffset.UtcNow.ToString("o");
            WriteAtomic(Path.Combine(_stateDirectory, "current.json"),
                Encoding.UTF8.GetBytes(_json.Serialize(current) + "\n"));
            recoveredRunId = runId;
            return true;
        }
        catch { return false; }
    }

    private object CleanupProgress()
    {
        try
        {
            // Lock order matches the Runner: repository -> durable state -> checkpoint store.
            // The checkpoint-store lock proves that no watcher is mutating checkpoint
            // state; a verified checkpoint itself remains recovery material and is preserved.
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
                            if (checkpoint == null || !checkpoint.ContainsKey("verified") ||
                                !Convert.ToBoolean(checkpoint["verified"]))
                                blockers.Add("latest-worktree-checkpoint");

                            string recoveredRunId = null;
                            string currentProgressPath = Path.Combine(_stateDirectory, "current.json");
                            if (SafeFileExists(currentProgressPath))
                            {
                                Dictionary<string, object> current = ReadJsonFile("current.json", 256000) as Dictionary<string, object>;
                                if (!RecoverStaleProgress(current, out recoveredRunId))
                                    blockers.Add("active_progress_run");
                            }

                            if (blockers.Count > 0)
                                return new Dictionary<string, object> {
                                    { "conflict", true }, { "blockers", blockers.ToArray() },
                                    { "message", "Durable task/checkpoint state is pending or ownership is unknown; cleanup was refused." }
                                };

                            // The authoritative progress projection and event history are
                            // durable recovery records. Cleanup removes only expendable temp files.
                            string[] cleanupNames = {
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
                                { "preserved", new[] { "current.json", "events.jsonl", "history.jsonl", "scorecard-baseline.json", "checkpoint files", "pending task/PR markers" } },
                                { "recovered_run_id", recoveredRunId },
                                { "recovery_state", recoveredRunId == null ? "unchanged" : "interrupted" }
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
