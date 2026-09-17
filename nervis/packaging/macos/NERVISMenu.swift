// NERVIS in the menu bar.
//
// Starts the stack in the repository this app was built from, shows whether the
// stack and the model runtimes are answering and how busy the Mac is, blinks while NERVIS has unread
// notifications, opens the dashboard in the default browser, and stops the stack
// when it quits. No Dock icon and no window: the menu bar icon is the whole of it,
// which is what the owner asked for on 12 September 2026.
//
// **Codex** (N1b, 14 September 2026) has a line under the model runtimes: its state, the tasks
// RAVIS runs for Clarvis with how long each has waited, and the ChatGPT plan's allowance. From
// there the owner can stop a task, re-test Codex's file rules and sign Codex in, each behind
// `tools/run.py codex …` — and nothing more: approving, answering and steering happen in Clarvis.
//
// **An app that holds no code.** The plan had a bundle per service — NERVIS M19,
// SIRVIS M22, RAVIS M24 — and a bundle that carries the services has to be rebuilt
// whenever one of them changes, which here is several times a day. This one carries a
// path and runs `tools/run.py`, the launcher the start and stop scripts already run,
// so a code change needs a restart and never a rebuild. The price is stated rather
// than hidden: move the repository and the app has to be rebuilt, because the path is
// written into its Info.plist when it is built.
//
// **It knows nothing about the stack.** Ports, services and how health is asked all
// stay in `tools/run.py`, and `run.py status --json` answers every question this menu
// shows. A second copy of that knowledge here would be the first thing to go stale.
//
// Built by build_app.sh beside this file.

import AppKit
import IOKit

// MARK: - What the launcher reports

/// One answer from `tools/run.py status --json`.
struct StackReport: Decodable {
    struct Service: Decodable {
        let name: String
        /// "stack" for what the launcher starts, "runtime" for LM Studio and Ollama, and
        /// "editor" for CLARVIS — listed with the stack, but not counted when the icon asks
        /// whether the stack is whole, because no editor window being open is not a fault.
        let group: String
        let answering: Bool
        /// For CLARVIS, how many editor windows have a live Bridge; nil for everything else.
        let windows: Int?
        /// The page the line opens in the browser, when there is one.
        let address: String?
        /// For a service the launcher owns that is running but not answering — past the time a
        /// start waits, so not merely booting — what the launcher found, such as "running as
        /// process 700 but not answering". Nil otherwise, and from a launcher older than this.
        let problem: String?
    }

    /// NERVIS's own machine reading, trimmed to what the menu shows. Every field is
    /// optional because a figure that was not measured is left out, never shown as zero.
    struct System: Decodable {
        let memoryTotalBytes: Double?
        let memoryAvailableBytes: Double?
        let swapUsedBytes: Double?
        let diskFreeBytes: Double?
        let loadAverage: [Double]?
        let cpuCount: Int?
        let thermalState: String?
    }

    /// NERVIS's notification centre, where every app's notices arrive: how many are
    /// unread, and the dashboard address of the screen that lists them. Nil when NERVIS
    /// is not answering, because there is then nobody to ask.
    struct Notifications: Decodable {
        let unread: Int
        let screen: String
    }

    /// Codex, OpenAI's coding agent, as RAVIS runs it for Clarvis: the launcher's trimmed reading
    /// of RAVIS's `GET /api/v1/codex` (`tools/run.py`, `_codex_block`). Every field but the state
    /// is optional, because a field the launcher couldn't fill is left out, never guessed.
    struct Codex: Decodable {
        /// One allowance window of the ChatGPT plan: what is left of it, and when it resets.
        struct Window: Decodable {
            let label: String?
            let remainingPercent: Double?
            let resetsAt: String?
        }

        /// One task RAVIS lists. A Clarvis-engine run has no id or turn: there is no Codex task
        /// to stop, only a project Clarvis's own engine is writing in.
        struct Run: Decodable {
            /// Set while RAVIS reopens the task's Codex conversation so a newly allowed site
            /// reaches it; carried as when it began only.
            struct Reopening: Decodable {
                let since: String?
            }

            let id: String?
            let turnId: String?
            let project: String?
            let state: String?
            let since: String?
            let ageMinutes: Double?
            let waitingMinutes: Double?
            let attachedWindows: Int?
            let model: String?
            let effort: String?
            let reopening: Reopening?
        }

        /// The Codex build RAVIS runs, which decides whether the file-rules re-test is offered.
        struct Runtime: Decodable {
            let version: String?
            let verdict: String?
            let strictRules: String?
        }

        /// RAVIS's state word, or `ravis_not_answering` when RAVIS couldn't be asked.
        let state: String
        let reason: String?
        let plan: String?
        let signedIn: Bool?
        let usageKnown: Bool?
        let stale: Bool?
        let windows: [Window]?
        /// Nil when RAVIS couldn't be asked: that says nothing about tasks, where [] says none.
        let runs: [Run]?
        let runtime: Runtime?
        let signInWaiting: Bool?
        /// The Codex card on the dashboard, which the Codex line opens.
        let address: String?
    }

    let services: [Service]
    let dashboard: String
    let system: System?
    let notifications: Notifications?
    /// Codex's entry: nil from a RAVIS that serves no Codex state, and nil when this build can't
    /// read the entry the launcher printed.
    let codex: Codex?

    private enum CodingKeys: String, CodingKey {
        case services, dashboard, system, notifications, codex
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        services = try container.decode([Service].self, forKey: .services)
        dashboard = try container.decode(String.self, forKey: .dashboard)
        system = try container.decodeIfPresent(System.self, forKey: .system)
        notifications = try container.decodeIfPresent(Notifications.self, forKey: .notifications)
        // A Codex entry this build can't read — from a launcher newer than the app, say — costs the
        // Codex line only, never the whole menu: the stack's lines still draw.
        codex = try? container.decodeIfPresent(Codex.self, forKey: .codex)
    }

    var stack: [Service] { services.filter { $0.group == "stack" } }
    /// The menu's Stack section: the stack with CLARVIS, in the launcher's order.
    var stackSection: [Service] { services.filter { $0.group == "stack" || $0.group == "editor" } }
    var runtimes: [Service] { services.filter { $0.group == "runtime" } }
    var stackIsUp: Bool { !stack.isEmpty && stack.allSatisfy { $0.answering } }

    static func decode(_ data: Data) -> StackReport? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(StackReport.self, from: data)
    }
}

/// One answer from `tools/run.py models --json`: LM Studio's installed models as SIRVIS
/// knows them, for the LM Studio submenu.
struct ModelsReport: Decodable {
    struct Model: Decodable {
        let key: String
        let name: String
        let format: String
        let quantization: String
        let sizeBytes: Double?
        let loaded: Bool
        /// Loaded from this menu, so the menu may unload it. A model loaded by anything
        /// else is shown as loaded and left alone.
        let heldByMenu: Bool
    }

    let available: Bool
    let models: [Model]
    let maxLoaded: Int?

    static func decode(_ data: Data) -> ModelsReport? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(ModelsReport.self, from: data)
    }
}

// MARK: - Running the launcher

/// Runs `work` on the main thread, served by the run loop rather than the main dispatch
/// queue.
///
/// **Why not `DispatchQueue.main.async`.** Quitting waits in a nested run loop until the
/// stack has stopped. When that quit began inside a main-queue block — the SIGTERM
/// handler's, found on 12 September 2026 — the main queue stays busy for the whole wait,
/// so the block that would have said "stopped, you may quit now" never ran: the app
/// stopped the stack and then sat in the menu bar for ever, its Quit item greyed out as
/// "Stopping the stack…". The run loop serves this in every common mode, the quit's own
/// wait included, whatever the dispatch queue is doing.
func onMainRunLoop(_ work: @escaping () -> Void) {
    CFRunLoopPerformBlock(CFRunLoopGetMain(), CFRunLoopMode.commonModes.rawValue, work)
    CFRunLoopWakeUp(CFRunLoopGetMain())
}

/// Runs `tools/run.py` in the repository, with the PATH a Terminal window would have.
final class Launcher: @unchecked Sendable {
    let repository: URL
    private let log: URL
    private var environment: [String: String] = [:]
    /// Released once the environment is known. Every run waits on it, so a quit that
    /// arrives in the first second still stops the stack with the right PATH.
    private let ready = DispatchSemaphore(value: 0)

    init(repository: URL) {
        self.repository = repository
        log = repository.appendingPathComponent(".run/menubar.log")
    }

    /// Works the environment out once, off the main thread, then lets runs through.
    func prepare(then done: @escaping @Sendable () -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            self.prepareNow()
            onMainRunLoop(done)
        }
    }

    /// `recordingRun` is false for `--print-menu`, which is a look at the menu and not a run of
    /// the app: writing "started" for it made the next real launch report that the run before
    /// it had ended without quitting — false evidence in the one log kept to say how runs end.
    func prepareNow(recordingRun: Bool = true) {
        environment = Launcher.loginEnvironment()
        if recordingRun { startLog() }
        ready.signal()
    }

    /// `start` or `stop`, with its output written to .run/menubar.log. Not piped:
    /// `start` leaves detached services behind, and a pipe one of them inherited would
    /// never reach its end, so reading it would wait for ever.
    func run(_ arguments: [String], then done: @escaping @Sendable (Int32) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.runNow(arguments, capture: false)
            onMainRunLoop { done(result.code) }
        }
    }

    /// A launcher command whose answer is read back — `models`, `load`, `unload`. None of
    /// them starts a process, so their output can be piped (see `run` for why that matters).
    func capture(_ arguments: [String], then done: @escaping @Sendable (Int32, Data) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.runNow(arguments, capture: true)
            onMainRunLoop { done(result.code, result.data) }
        }
    }

    /// One `status --json` answer, or nil when the launcher could not give one.
    func report(then done: @escaping @Sendable (StackReport?) -> Void) {
        DispatchQueue.global(qos: .utility).async {
            let result = self.runNow(["status", "--json"], capture: true)
            let report = result.code == 0 ? StackReport.decode(result.data) : nil
            onMainRunLoop { done(report) }
        }
    }

    /// Blocking. Waits for the environment first; `capture` returns stdout rather than
    /// logging it, and is only for `status`, which starts nothing.
    func runNow(_ arguments: [String], capture: Bool) -> (code: Int32, data: Data) {
        ready.wait()
        ready.signal()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", "tools/run.py"] + arguments
        process.currentDirectoryURL = repository
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        let logHandle = try? FileHandle(forWritingTo: log)
        _ = try? logHandle?.seekToEnd()
        let pipe = capture ? Pipe() : nil
        process.standardOutput = pipe ?? logHandle ?? FileHandle.nullDevice
        process.standardError = logHandle ?? FileHandle.nullDevice
        defer { try? logHandle?.close() }
        do {
            try process.run()
        } catch {
            return (-1, Data())
        }
        // Read before waiting: an answer larger than the pipe's buffer would otherwise
        // leave the launcher blocked on a write this side never drains.
        let data = pipe?.fileHandleForReading.readDataToEndOfFile() ?? Data()
        process.waitUntilExit()
        return (process.terminationStatus, data)
    }

    /// The PATH a Terminal would have. An app opened from Finder inherits launchd's bare
    /// /usr/bin:/bin:/usr/sbin:/sbin, where neither pyenv's python3 nor Homebrew's
    /// ollama nor ~/.local/bin's code-server can be found — the stack would come up
    /// without half of itself and say nothing about it. So the user's own login shell
    /// is asked once, behind a marker so anything its startup files print is ignored,
    /// and a fixed list stands in if it does not answer within five seconds.
    static func loginEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let fallback = ["\(home)/.pyenv/shims", "\(home)/.local/bin", "/opt/homebrew/bin",
                        "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        environment["PATH"] = shellPath(environment["SHELL"] ?? "/bin/zsh") ?? fallback.joined(separator: ":")
        return environment
    }

    private static func shellPath(_ shell: String) -> String? {
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: shell)
        probe.arguments = ["-l", "-i", "-c", "printf '\\n__NERVIS_PATH__%s' \"$PATH\""]
        let output = Pipe()
        probe.standardOutput = output
        probe.standardError = FileHandle.nullDevice
        probe.standardInput = FileHandle.nullDevice
        guard (try? probe.run()) != nil else { return nil }
        let deadline = Date().addingTimeInterval(5)
        while probe.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        guard !probe.isRunning else {
            probe.terminate()
            return nil
        }
        let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        guard let marker = text.range(of: "__NERVIS_PATH__") else { return nil }
        let path = text[marker.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
        return path.isEmpty ? nil : path
    }

    static let startedLine = "NERVIS menu bar app started"
    static let exitedLine = "NERVIS menu bar app exiting"

    /// Opens this run's part of the log, and says whether the run before it ended by quitting.
    ///
    /// **Appended, not started afresh.** The log used to be rewritten at every launch, which
    /// erased the one thing worth reading after the app vanished: how the last run ended. On
    /// 12 September 2026 it was found not running with the stack still up, no crash report and
    /// nothing in the system log, and the next launch had already wiped its log. Now each run
    /// records its quit, and a launch that finds the last run never recorded one says so. A
    /// log past half a megabyte is moved to `menubar.previous.log` first, so it cannot grow
    /// for ever.
    private func startLog() {
        let manager = FileManager.default
        try? manager.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        let before = (try? String(contentsOf: log, encoding: .utf8)) ?? ""
        let lastRun = before.components(separatedBy: Launcher.startedLine).last ?? ""
        if before.utf8.count > 512_000 {
            let older = log.deletingLastPathComponent().appendingPathComponent("menubar.previous.log")
            if manager.fileExists(atPath: older.path) {
                _ = try? manager.replaceItemAt(older, withItemAt: log)
            } else {
                try? manager.moveItem(at: log, to: older)
            }
        }
        record("\(Launcher.startedLine) for \(repository.path)")
        if !before.isEmpty && !lastRun.contains(Launcher.exitedLine) {
            record("the previous run ended without quitting — killed, force-quit or crashed; "
                + "its last lines are just above, or in menubar.previous.log")
        }
    }

    /// One timestamped line in the app's log.
    func record(_ line: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        let data = Data("\(stamp) \(line)\n".utf8)
        if let handle = try? FileHandle(forWritingTo: log) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        } else {
            try? data.write(to: log)
        }
    }
}

// MARK: - The icon

enum Mark {
    /// The NERVIS mark — a tilted square with a smaller one for its pupil — as a menu
    /// bar template image. Drawn in code rather than shipped as a file, from the same
    /// proportions as generate_icon.py: the pupil's half-diagonal is 0.32 of the outer
    /// diamond's (96 of 300 there).
    ///
    /// A template image is black and transparency only, and macOS paints it white on a
    /// dark menu bar and black on a light one — how every menu bar icon stays legible on
    /// both. **The pupil is solid while the whole stack answers and faint otherwise**, so
    /// the icon says when the menu is worth opening.
    ///
    /// `pupil: false` is the blink. While NERVIS holds unread notifications the pupil goes
    /// out for a quarter of a second in every second and a half — an eye blinking, which
    /// the menu bar has room for where a numbered badge would not fit.
    static func image(whole: Bool, pupil: Bool = true, size: CGFloat = 18) -> NSImage {
        let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
            let center = NSPoint(x: rect.midX, y: rect.midY)
            let outer = rect.width * 0.40
            func diamond(_ radius: CGFloat) -> NSBezierPath {
                let path = NSBezierPath()
                path.move(to: NSPoint(x: center.x, y: center.y + radius))
                path.line(to: NSPoint(x: center.x + radius, y: center.y))
                path.line(to: NSPoint(x: center.x, y: center.y - radius))
                path.line(to: NSPoint(x: center.x - radius, y: center.y))
                path.close()
                return path
            }
            let ring = diamond(outer)
            ring.lineWidth = rect.width * 0.09
            ring.lineJoinStyle = .round
            NSColor.black.setStroke()
            ring.stroke()
            if pupil {
                NSColor.black.withAlphaComponent(whole ? 1 : 0.35).setFill()
                diamond(outer * 0.32).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}

// MARK: - The machine figures

/// CPU use across all cores, as a percentage of the time since it was last asked.
///
/// From the kernel's own tick counters (`host_statistics`), which Activity Monitor's CPU
/// graph is drawn from too: busy ticks over all ticks between two readings. The first
/// reading has nothing to compare with, so it gives nil rather than a made-up figure.
final class CPUMeter {
    private let host = mach_host_self()
    private var last: (busy: UInt64, total: UInt64)?

    deinit { mach_port_deallocate(mach_task_self_, host) }

    func percent() -> Int? {
        var load = host_cpu_load_info()
        var count = mach_msg_type_number_t(
            MemoryLayout<host_cpu_load_info_data_t>.stride / MemoryLayout<integer_t>.stride)
        let result = withUnsafeMutablePointer(to: &load) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics(host, HOST_CPU_LOAD_INFO, $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return nil }
        let ticks = load.cpu_ticks  // user, system, idle, nice
        let busy = UInt64(ticks.0) + UInt64(ticks.1) + UInt64(ticks.3)
        let total = busy + UInt64(ticks.2)
        defer { last = (busy, total) }
        guard let last, total > last.total else { return nil }
        return Int((Double(busy - last.busy) / Double(total - last.total) * 100).rounded())
    }
}

/// GPU use as the graphics driver reports it to the I/O registry — the "Device
/// Utilization %" that `ioreg -c IOAccelerator` prints, which needs no administrator
/// rights, unlike `powermetrics`. The busiest GPU if there is more than one; nil when no
/// driver publishes the figure.
enum GPUMeter {
    static func percent() -> Int? {
        var iterator: io_iterator_t = 0
        guard IOServiceGetMatchingServices(
            kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &iterator) == KERN_SUCCESS
        else { return nil }
        defer { IOObjectRelease(iterator) }
        var busiest: Int?
        var service = IOIteratorNext(iterator)
        while service != 0 {
            let property = IORegistryEntryCreateCFProperty(
                service, "PerformanceStatistics" as CFString, kCFAllocatorDefault, 0)
            if let statistics = property?.takeRetainedValue() as? [String: Any],
               let utilization = (statistics["Device Utilization %"] as? NSNumber)?.intValue {
                busiest = max(busiest ?? 0, utilization)
            }
            IOObjectRelease(service)
            service = IOIteratorNext(iterator)
        }
        return busiest
    }
}

@MainActor
enum Figures {
    private static let gibibyte = 1_073_741_824.0
    /// CPU or GPU use above this is drawn in red — the owner's threshold, 12 September 2026.
    static let busy = 85

    /// The machine figures the menu shows, each only when it was measured. CPU and GPU come
    /// from this app's own meters, so they show even while the stack is down; memory, swap,
    /// disk and heat from NERVIS's reading, so the menu and the dashboard agree on them.
    static func lines(cpu: Int?, gpu: Int?, system: StackReport.System?) -> [NSAttributedString] {
        var lines: [NSAttributedString] = []
        if let cpu { lines.append(percentage("CPU", cpu)) }
        if let gpu { lines.append(percentage("GPU", gpu)) }
        if let total = system?.memoryTotalBytes, let available = system?.memoryAvailableBytes, total > 0 {
            let used = total - available
            lines.append(plain("Memory \(Int((used / total * 100).rounded()))% · \(gb(used)) of \(gb(total)) GB"))
        }
        var rest: [String] = []
        if let swap = system?.swapUsedBytes { rest.append("Swap \(gb(swap)) GB") }
        if let disk = system?.diskFreeBytes { rest.append(String(format: "%.0f GB disk free", disk / gibibyte)) }
        if let thermal = system?.thermalState { rest.append("thermal \(thermal)") }
        if !rest.isEmpty { lines.append(plain(rest.joined(separator: " · "))) }
        return lines
    }

    /// In the menu's own text colour — white on a dark menu, black on a light one.
    private static func plain(_ text: String) -> NSAttributedString {
        NSAttributedString(string: text, attributes: [.foregroundColor: NSColor.labelColor,
                                                      .font: NSFont.menuFont(ofSize: 0)])
    }

    /// "CPU 92%", with the figure in red above `busy`.
    private static func percentage(_ label: String, _ value: Int) -> NSAttributedString {
        let line = NSMutableAttributedString(attributedString: plain("\(label) "))
        let colour = value > busy ? NSColor.systemRed : NSColor.labelColor
        line.append(NSAttributedString(string: "\(value)%", attributes: [.foregroundColor: colour,
                                                                         .font: NSFont.menuFont(ofSize: 0)]))
        return line
    }

    private static func gb(_ bytes: Double) -> String { String(format: "%.1f", bytes / gibibyte) }
}

// MARK: - Codex's line

/// What the menu says about Codex, worked out from the launcher's reading and nothing else
/// (design §7.1): the row's words and its dot, the lines under it, and what each task offers.
///
/// **Never red.** Codex signed out, paused or used up is something for the owner to do, not a
/// fault in the stack: orange where the owner is needed, blue while tasks work, green when Codex
/// is ready and grey when nothing can be said. And **unknown is never a number**: an allowance
/// RAVIS hasn't read is said to be unread, never "0% left".
struct CodexLine {
    enum Tone { case ready, working, needsYou, unknown }

    /// What a task's submenu offers: Stop for that task's id, or why there is nothing to stop.
    enum StopOffer: Equatable {
        case offer(String)
        case unavailable(String)
    }

    let title: String
    let tone: Tone

    /// The task states RAVIS lists (`codex-state.json` → `run_states`), as the menu says them.
    static let taskWords: [String: String] = [
        "running": "running",
        "waiting_on_you": "waiting for your answer",
        "paused_unanswered": "paused — waited 30 min for an answer",
        "paused_for_update": "paused — Codex updated",
        "completed_needs_review": "finished — needs review",
        "uncertain": "uncertain — cut off mid-step",
        "leftover": "stopped, with processes left over",
        "clarvis_engine": "Clarvis's own engine, not Codex",
    ]
    /// What a Stop can reach. RAVIS stops a task that is starting, running, waiting or already
    /// stopping, and lists all of those as running or waiting; anything else has stopped.
    static let stoppable: Set<String> = ["running", "waiting_on_you"]
    /// Tasks held for the owner in Clarvis: a question that waited too long, a review, a restart.
    static let needingYou: Set<String> = [
        "paused_unanswered", "paused_for_update", "completed_needs_review", "uncertain", "leftover",
    ]
    /// Codex's own states, as the row says them when no task is listed.
    static let stateWords: [String: String] = [
        "checking": "checking…",
        "not_installed": "not installed",
        "not_available": "not available",
        "untested_version": "paused — needs re-testing",
        "runtime_down": "process restarting",
        "signed_out": "signed out",
        "sign_in_expired": "sign-in expired",
        "account_changed": "different account — confirm it on the dashboard",
        "quota_exhausted": "allowance used up",
        "signed_in": "signed in",
        "ravis_not_answering": "not known — RAVIS isn't answering",
    ]
    /// The states that need the owner, drawn orange; the others, with nothing running, are grey.
    static let orange: Set<String> = [
        "signed_out", "sign_in_expired", "account_changed", "untested_version", "quota_exhausted",
    ]

    init(_ codex: StackReport.Codex) {
        let tasks = (codex.runs ?? []).filter { $0.state != "clarvis_engine" }
        let waiting = tasks.filter { $0.state == "waiting_on_you" }.count
        let needing = tasks.filter { CodexLine.needingYou.contains($0.state ?? "") }.count
        let head = CodexLine.headline(codex, tasks: tasks.count, waiting: waiting, needing: needing)
        title = head.0
        tone = head.1
    }

    /// The row: the tasks first, since they are what the owner can act on; then, with none, the
    /// state and what is left of the tightest allowance window.
    static func headline(
        _ codex: StackReport.Codex, tasks: Int, waiting: Int, needing: Int
    ) -> (String, Tone) {
        let count = tasks == 1 ? "1 task" : "\(tasks) tasks"
        if waiting > 0 { return ("Codex · \(count) · \(waiting) waiting for your answer", .needsYou) }
        if needing > 0 { return ("Codex · \(count) · \(needing) \(needing == 1 ? "needs" : "need") you", .needsYou) }
        if tasks > 0 { return ("Codex · \(count) running", .working) }
        if codex.signInWaiting == true { return ("Codex · signing in — finish in the browser", .needsYou) }
        let tight = tightest(codex)
        switch codex.state {
        case "signed_in":
            guard let tight, let left = tight.remainingPercent else {
                return ("Codex · signed in · allowance not read yet", .ready)
            }
            let old = codex.stale == true ? " · an old reading" : ""
            return ("Codex · \(percent(left)) left · resets \(clock(tight.resetsAt))\(old)", .ready)
        case "quota_exhausted":
            return ("Codex · allowance used up" + (tight.map { " · resets \(clock($0.resetsAt))" } ?? ""), .needsYou)
        default:
            return ("Codex · \(stateWords[codex.state] ?? codex.state)", orange.contains(codex.state) ? .needsYou : .unknown)
        }
    }

    /// One task under the row, as design §7.1 words it: "add-utc-demo — waiting for your answer ·
    /// 12 min, no editor open".
    static func taskLine(_ run: StackReport.Codex.Run, stopping: [String: String]) -> String {
        let project = run.project ?? "a folder RAVIS didn't name"
        let state = run.state ?? ""
        if isStopping(run, stopping: stopping) { return "\(project) — stopping…" }
        guard state != "clarvis_engine" else { return "\(project) — \(taskWords[state] ?? state)" }
        let words = run.reopening != nil ? "reconnecting Codex (up to two minutes)" : taskWords[state] ?? state
        var line = "\(project) — \(words)"
        if let minutesShown = run.waitingMinutes ?? run.ageMinutes { line += " · \(minutes(minutesShown))" }
        if run.attachedWindows == 0 { line += ", no editor open" }
        return line
    }

    /// The quiet lines in a task's submenu: the model and effort it runs at, and a reconnect.
    static func taskNotes(_ run: StackReport.Codex.Run) -> [String] {
        var notes: [String] = []
        if run.state != "clarvis_engine", run.model != nil || run.effort != nil {
            let effort = run.effort.map { "\($0) effort" } ?? "its default effort"
            notes.append("Runs \(run.model ?? "Codex's default model") at \(effort)")
        }
        if run.reopening != nil {
            notes.append("Reconnecting Codex so a newly allowed site works — up to two minutes")
        }
        return notes
    }

    /// Stop this task…, only where RAVIS would stop something, with the id, folder and turn its
    /// confirmation sends back; otherwise the reason there is nothing to stop.
    static func stop(_ run: StackReport.Codex.Run, stopping: [String: String]) -> StopOffer {
        if run.state == "clarvis_engine" { return .unavailable("Clarvis's own engine — stop it in the editor") }
        guard stoppable.contains(run.state ?? "") else {
            return .unavailable("Nothing to stop — open the project in Clarvis to review it")
        }
        guard let id = run.id, run.project != nil, run.turnId != nil else {
            return .unavailable("Stop this task… once Codex begins its first step")
        }
        return isStopping(run, stopping: stopping) ? .unavailable("Stopping…") : .offer(id)
    }

    /// Whether this menu asked RAVIS to stop the task on the turn RAVIS still lists it working.
    static func isStopping(_ run: StackReport.Codex.Run, stopping: [String: String]) -> Bool {
        guard let id = run.id, let turn = run.turnId else { return false }
        return stopping[id] == turn && stoppable.contains(run.state ?? "")
    }

    /// Sign in is offered where RAVIS would start one: nobody signed in, and Codex either signed
    /// out or paused on a build RAVIS runs a process for. Any other state, RAVIS refuses it.
    static func canSignIn(_ codex: StackReport.Codex) -> Bool {
        guard codex.signedIn == false else { return false }
        if codex.state == "signed_out" || codex.state == "sign_in_expired" { return true }
        return codex.state == "untested_version" && codex.runtime?.verdict != "untested"
    }

    /// The window with the least left, among those RAVIS gave a figure for. The Codex row shows only
    /// this one: a line per allowance window under the row repeated it word for word with the one
    /// weekly window a Plus plan has, so the owner had them removed (NERVIS 0.29.1). Every window
    /// is on the dashboard's Codex card.
    static func tightest(_ codex: StackReport.Codex) -> StackReport.Codex.Window? {
        guard codex.usageKnown == true else { return nil }
        return (codex.windows ?? []).filter { $0.remainingPercent != nil }
            .min { ($0.remainingPercent ?? 0) < ($1.remainingPercent ?? 0) }
    }

    static func percent(_ value: Double) -> String { "\(Int(value.rounded()))%" }

    static func minutes(_ value: Double) -> String {
        let whole = Int(value.rounded())
        return whole < 60 ? "\(whole) min" : "\(whole / 60) h \(whole % 60) min"
    }

    /// A time from RAVIS in local time, 24-hour: the clock alone today, the day and date otherwise.
    static func clock(_ iso: String?) -> String {
        guard let iso, let date = parsedDate(iso) else { return "at a time not given" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "EEE d MMM HH:mm"
        return formatter.string(from: date)
    }

    static func parsedDate(_ iso: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        if let date = formatter.date(from: iso) { return date }
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: iso)
    }
}

extension CodexLine.Tone {
    var colour: NSColor {
        switch self {
        case .ready: return .systemGreen
        case .working: return .systemBlue
        case .needsYou: return .systemOrange
        case .unknown: return .tertiaryLabelColor
        }
    }
}

/// The menu's sentences for Codex's dialogs and for each exit code of `run.py codex …`, as the
/// `launcher` sections of RAVIS's contract fixtures name them: `owner-stop.json` for Stop,
/// `codex-admin.json` for the re-test, and the sign-in's from design §3.7. An exit the contract
/// doesn't name is 1, and then the launcher's own sentence is shown.
enum CodexWords {
    static func stopQuestion(_ run: StackReport.Codex.Run) -> String {
        let state = CodexLine.taskWords[run.state ?? ""] ?? "running"
        let started = run.since.flatMap { CodexLine.parsedDate($0) == nil ? nil : CodexLine.clock($0) }
        let opening = started.map { "It started at \($0) and is \(state)." } ?? "It is \(state)."
        return opening + " Stopping ends its current step and the commands it started. Codex's work "
            + "so far stays in the project; open the project in an editor to review and save it. "
            + "Nothing is answered or approved."
    }

    static func stopExit(_ code: Int32, _ error: String?) -> String {
        switch code {
        case 6: return "RAVIS isn't answering, so the stop didn't reach it. The task may still be running; try again once RAVIS is back."
        case 8: return "That task changed; the menu has been refreshed. Look at the task again, and stop it again if you still want to."
        case 9: return "That task isn't running any more, so there was nothing to stop."
        case 10: return "RAVIS refused to stop it. " + (error ?? "It gave no reason.")
        case 2: return "The menu couldn't name the task to the launcher: its id, folder or turn was missing. The menu has been refreshed; try again."
        default: return error.map { "The launcher couldn't stop it. \($0)" }
            ?? "The launcher couldn't stop it and gave no reason; .run/menubar.log may say more."
        }
    }

    static func retestQuestion(version: String?) -> (String, String) {
        let build = version.map { "Codex \($0)" } ?? "Codex"
        return ("Re-test \(build)'s file rules?",
                "Codex is asked to run four fixed test commands in two throwaway folders with no network "
                + "— reading a decoy key file, and writing outside its folder — and RAVIS approves exactly "
                + "those, so only the file rules can stop them. It uses one short Codex turn from your "
                + "plan's allowance, at low effort, and takes at most 5 minutes. No Codex task can run "
                + "meanwhile, and none of your projects is touched.")
    }

    static func retestResult(_ code: Int32, _ error: String?) -> (String, String) {
        switch code {
        case 0: return ("The file rules held", "Every rule that keeps Codex away from your key files held on this build, so Codex can take tasks again.")
        case 11: return ("A file rule didn't hold", "On this Codex build a rule that keeps Codex away from your key files didn't hold, so Codex stays paused for tasks, and what happens next is your decision. Nothing ran in your projects: the test used throwaway folders.")
        case 12: return ("The re-test couldn't tell", "Codex didn't follow the four test commands, or the test hit its time or step limit. The file rules stay unproven and Codex stays paused; you can try again.")
        case 7: return ("A Codex task is running", "The re-test runs only while no Codex task is live. Stop or finish the task, then try again.")
        case 6: return ("RAVIS isn't answering", "The re-test didn't start. Try again once RAVIS is running.")
        case 10: return ("RAVIS refused the re-test", error ?? "It gave no reason.")
        default: return ("The re-test didn't finish", error ?? "The launcher gave no reason; .run/menubar.log may say more.")
        }
    }

    static func signInExit(_ code: Int32, _ error: String?) -> String {
        switch code {
        case 3: return "Codex is already signed in."
        case 4: return "Codex isn't installed, or it is a build RAVIS hasn't tested, so there is nothing to sign in to yet."
        case 5: return "Another program is holding the sign-in ports 1455 and 1457 — usually the ChatGPT app's own Codex signing in. Finish or close that sign-in, then try again."
        case 6: return "RAVIS isn't answering, so the sign-in didn't start."
        case 7: return "Codex is working on a task; sign in after it pauses."
        case 10: return "RAVIS refused. " + (error ?? "It gave no reason.")
        default: return error ?? "The launcher gave no reason; .run/menubar.log may say more."
        }
    }

    /// The `error` sentence of the launcher's one JSON line, when it printed one.
    static func error(_ data: Data) -> String? {
        let answer = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return answer?["error"] as? String
    }
}

// MARK: - The menu bar icon and its menu

@MainActor
final class MenuBar: NSObject, NSApplicationDelegate, NSMenuDelegate {
    enum Phase { case starting, running, stopping, stopped }

    private let launcher: Launcher
    private var statusItem: NSStatusItem?
    let menu = NSMenu()
    var report: StackReport?
    var phase = Phase.starting
    private var starting = false
    private var refreshing = false
    private var timer: Timer?
    private var terminationSignal: DispatchSourceSignal?
    private var blinkTimer: Timer?
    private var blinkTick = 0
    private let cpuMeter = CPUMeter()
    private(set) var cpuPercent: Int?
    private(set) var gpuPercent: Int?
    /// LM Studio's installed models, read every thirty seconds and whenever the menu opens.
    var models: ModelsReport?
    /// The model being loaded or unloaded right now, drawn as working and not clickable.
    private var modelBusy: String?
    private var modelsRefreshing = false
    private var menuOpen = false
    private var menuStale = false
    private var ticks = 0
    private var renewTimer: Timer?
    /// Codex tasks this menu asked RAVIS to stop, by id, with the turn the stop confirmed: drawn
    /// as stopping until RAVIS no longer lists that task working on that turn.
    private var codexStopping: [String: String] = [:]
    /// True while the file-rules re-test runs, which takes up to six minutes.
    private var codexRetesting = false
    /// True while the launcher asks RAVIS to start or cancel a sign-in.
    private var codexSigningIn = false

    init(launcher: Launcher) {
        self.launcher = launcher
        super.init()
        menu.autoenablesItems = false
        menu.delegate = self
        sampleMachine()  // the CPU meter's first reading, which the next one is measured against
    }

    /// Reads the CPU and GPU meters. A CPU reading that has nothing to compare with keeps
    /// the last percentage rather than blanking it.
    func sampleMachine() {
        cpuPercent = cpuMeter.percent() ?? cpuPercent
        gpuPercent = GPUMeter.percent()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.menu = menu
        statusItem = item
        redraw()
        // `kill` quits the way the menu does, stack and all. AppKit ignores SIGTERM on
        // its own, so without this the app would simply vanish and leave the services
        // running with nothing in the menu bar to stop them.
        signal(SIGTERM, SIG_IGN)
        let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        // Handed to the run loop rather than called here: a quit started inside this
        // dispatch block would wait for the stack with the main queue held (see
        // `onMainRunLoop`).
        terminate.setEventHandler { [weak self] in
            onMainRunLoop {
                MainActor.assumeIsolated {
                    self?.launcher.record("told to quit by a signal (SIGTERM)")
                    NSApp.terminate(nil)
                }
            }
        }
        terminate.resume()
        terminationSignal = terminate
        launcher.prepare { [weak self] in
            MainActor.assumeIsolated { self?.startStack() }
        }
    }

    private func startStack() {
        starting = true
        refresh()
        launcher.run(["start"]) { [weak self] _ in
            MainActor.assumeIsolated { self?.startFinished() }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        // The owner chose that a model loaded from the menu stays loaded until it is
        // unloaded there or NERVIS quits. SIRVIS's lease lasts an hour, so the app renews
        // the menu's leases every ten minutes while it runs; if the app dies, they lapse
        // within the hour and SIRVIS unloads the models by itself.
        renewTimer = Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.launcher.run(["renew"]) { _ in } }
        }
    }

    /// Every ten seconds the stack; every thirty the models, which cost SIRVIS a look at
    /// LM Studio's CLI and change far less often.
    private func tick() {
        ticks += 1
        refresh()
        if ticks % 3 == 0 { refreshModels() }
    }

    private func startFinished() {
        starting = false
        if phase == .stopping {
            // Quit arrived while the stack was still starting. Stopping waited for the
            // start, because stop reads the record start writes, and a stop that ran
            // alongside it would miss whatever started after it looked.
            stopThenTerminate()
            return
        }
        phase = .running
        refresh()
        refreshModels()
    }

    /// The menu is drawn from the last readings — at most ten seconds old — and is not
    /// rebuilt while it is open: rebuilding would snap shut a submenu somebody is reading,
    /// such as LM Studio's list of models. Fresh readings are asked for as it opens, and
    /// whatever they change is drawn once it closes.
    func menuWillOpen(_ menu: NSMenu) {
        menuOpen = true
        refresh()
        refreshModels()
    }

    func menuDidClose(_ menu: NSMenu) {
        menuOpen = false
        if menuStale { rebuildMenu() }
    }

    func refresh() {
        guard !refreshing, phase != .stopping, phase != .stopped else { return }
        refreshing = true
        launcher.report { [weak self] report in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.refreshing = false
                if let report {
                    self.report = report
                    self.forgetFinishedStops()
                }
                self.sampleMachine()
                self.redraw()
            }
        }
    }

    /// Quitting stops the stack first, however the quit arrives: the menu, a logout,
    /// or `osascript -e 'quit app "NERVIS"'`.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        switch phase {
        case .stopped: return .terminateNow
        case .stopping: return .terminateCancel
        case .starting, .running: break
        }
        phase = .stopping
        launcher.record("quit requested — releasing the menu's models and stopping the stack first")
        timer?.invalidate()
        redraw()
        if !starting { stopThenTerminate() }
        return .terminateLater
    }

    private func stopThenTerminate() {
        launcher.run(["stop"]) { [weak self] code in
            MainActor.assumeIsolated {
                self?.launcher.record("\(Launcher.exitedLine); the stack's stop finished with status \(code)")
                self?.phase = .stopped
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
    }

    @objc private func openDashboard() {
        guard let address = report?.dashboard, let url = URL(string: address) else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openAddress(_ sender: NSMenuItem) {
        guard let url = sender.representedObject as? URL else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openNotifications() {
        guard let address = report?.notifications?.screen, let url = URL(string: address) else { return }
        NSWorkspace.shared.open(url)
    }

    /// LM Studio's bundle identifier, read from its Info.plist on 12 September 2026.
    static let lmStudio = "ai.elementlabs.lmstudio"

    @objc private func openApplication(_ sender: NSMenuItem) {
        guard let app = sender.representedObject as? URL else { return }
        NSWorkspace.shared.openApplication(at: app, configuration: NSWorkspace.OpenConfiguration())
    }

    /// Asks LM Studio to quit, the way its own Quit does. With a model loaded it asks first:
    /// quitting unloads every model, so whatever was using one — a chat, a benchmark, a
    /// SIRVIS session — stops with it.
    @objc private func quitLMStudio() {
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: MenuBar.lmStudio)
        guard !running.isEmpty else { return }
        let loaded = models?.models.filter { $0.loaded }.map { $0.name } ?? []
        if !loaded.isEmpty {
            NSApp.activate()
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Quit LM Studio with \(loaded.count == 1 ? "a model" : "\(loaded.count) models") loaded?"
            alert.informativeText = "Quitting unloads \(loaded.joined(separator: ", ")), "
                + "and anything using \(loaded.count == 1 ? "it" : "them") stops."
            alert.addButton(withTitle: "Quit LM Studio")
            alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }
        running.forEach { $0.terminate() }
        // The model list says "not running" on its next read rather than keeping stale ticks.
        refreshModels()
    }

    static let gibibyte = 1_073_741_824.0
    /// Room a model needs beyond its file — context and working memory — when the menu
    /// judges whether it probably fits in the memory free right now.
    static let loadHeadroom = 1.2

    /// LM Studio's submenu: open the app, then every installed model, loadable through SIRVIS.
    private func lmStudioMenu() -> NSMenu {
        let submenu = NSMenu()
        submenu.autoenablesItems = false
        if let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: MenuBar.lmStudio) {
            let open = NSMenuItem(title: "Open LM Studio", action: #selector(openApplication(_:)), keyEquivalent: "")
            open.representedObject = app
            open.target = self
            submenu.addItem(open)
        }
        // Only while it runs: a Quit item for an app that isn't open would do nothing.
        if !NSRunningApplication.runningApplications(withBundleIdentifier: MenuBar.lmStudio).isEmpty {
            let quit = NSMenuItem(title: "Quit LM Studio", action: #selector(quitLMStudio), keyEquivalent: "")
            quit.target = self
            submenu.addItem(quit)
        }
        submenu.addItem(NSMenuItem.sectionHeader(title: "Load through SIRVIS"))
        guard let models, models.available else {
            let sirvisUp = report?.stack.first { $0.name == "SIRVIS" }?.answering == true
            submenu.addItem(note(sirvisUp ? "Reading the installed models…" : "SIRVIS is not running"))
            return submenu
        }
        if models.models.isEmpty { submenu.addItem(note("No models installed")) }
        models.models.forEach { submenu.addItem(modelItem($0)) }
        submenu.addItem(.separator())
        submenu.addItem(note("✓ loaded from this menu, click to unload · – loaded by something else"))
        return submenu
    }

    /// One model: click to load, a tick and click to unload when this menu loaded it, a dash
    /// and no click when something else did, and "loading…" while SIRVIS works on it.
    private func modelItem(_ model: ModelsReport.Model) -> NSMenuItem {
        let font = NSFont.menuFont(ofSize: 0)
        let size = model.sizeBytes.map { String(format: "%.1f GB", $0 / MenuBar.gibibyte) } ?? ""
        let details = [model.format.uppercased(), model.quantization, size].filter { !$0.isEmpty }
        let title = NSMutableAttributedString(string: model.name, attributes: [.font: font])
        let working = modelBusy == model.key
        let trailing = working ? (model.heldByMenu ? "unloading…" : "loading…") : details.joined(separator: " · ")
        if !trailing.isEmpty {
            title.append(NSAttributedString(string: "   " + trailing,
                attributes: [.foregroundColor: NSColor.secondaryLabelColor, .font: font]))
        }
        let item = NSMenuItem(title: model.name, action: nil, keyEquivalent: "")
        item.attributedTitle = title
        item.representedObject = model.key
        item.target = self
        if working {
            item.isEnabled = false
        } else if model.heldByMenu {
            item.state = .on
            item.action = #selector(unloadModel(_:))
            item.toolTip = "Loaded from this menu. Click to unload it."
        } else if model.loaded {
            item.state = .mixed
            item.isEnabled = false
            item.toolTip = "Loaded by something else, which this menu leaves alone."
        } else {
            item.action = #selector(loadModel(_:))
            item.isEnabled = modelBusy == nil
            item.toolTip = "Load through SIRVIS."
        }
        return item
    }

    @objc private func loadModel(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? String,
              let model = models?.models.first(where: { $0.key == key }),
              fitsOrConfirmed(model) else { return }
        change(model, ["load", key])
    }

    @objc private func unloadModel(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? String,
              let model = models?.models.first(where: { $0.key == key }) else { return }
        change(model, ["unload", key])
    }

    /// "Ask me first", as the owner chose: a model whose file, with room to work, is larger
    /// than the memory free right now loads only after a dialog says so. A model of unknown
    /// size, or a moment when NERVIS cannot say what is free, loads without asking — there is
    /// nothing to judge with, and refusing would lock the menu exactly when a figure is missing.
    private func fitsOrConfirmed(_ model: ModelsReport.Model) -> Bool {
        guard let size = model.sizeBytes, let free = report?.system?.memoryAvailableBytes,
              size * MenuBar.loadHeadroom > free else { return true }
        NSApp.activate()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "\(model.name) probably won't fit in free memory"
        alert.informativeText = String(
            format: "Its file is %.1f GB, and with room to work it needs about %.1f GB. %.1f GB is free right now. "
                + "Loading it anyway can make the Mac swap and slow everything down until it is unloaded.",
            size / MenuBar.gibibyte, size * MenuBar.loadHeadroom / MenuBar.gibibyte, free / MenuBar.gibibyte)
        alert.addButton(withTitle: "Load Anyway")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func change(_ model: ModelsReport.Model, _ arguments: [String]) {
        modelBusy = model.key
        redraw()
        launcher.capture(arguments) { [weak self] code, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.modelBusy = nil
                if code != 0 { self.explain(data, model: model, loading: arguments.first == "load") }
                self.refreshModels()
            }
        }
    }

    /// SIRVIS's own words when it would not do what was asked — two models already loaded,
    /// the runtime not answering — rather than a click that silently did nothing.
    private func explain(_ data: Data, model: ModelsReport.Model, loading: Bool) {
        let answer = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        NSApp.activate()
        let alert = NSAlert()
        alert.messageText = loading ? "SIRVIS did not load \(model.name)" : "SIRVIS did not unload \(model.name)"
        alert.informativeText = (answer?["error"] as? String)
            ?? "The launcher gave no reason; .run/menubar.log may say more."
        alert.runModal()
    }

    func refreshModels() {
        guard !modelsRefreshing, phase != .stopping, phase != .stopped else { return }
        modelsRefreshing = true
        launcher.capture(["models", "--json"]) { [weak self] _, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.modelsRefreshing = false
                if let fresh = ModelsReport.decode(data) { self.models = fresh }
                self.redraw()
            }
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    var headline: String {
        switch phase {
        case .stopping: return "Stopping the stack…"
        case .stopped: return "The stack is stopped"
        case .starting, .running: break
        }
        guard let report else { return "Starting the stack…" }
        if report.stackIsUp { return "The stack is running" }
        if phase == .starting { return "Starting the stack…" }
        return "Not answering: " + report.stack.filter { !$0.answering }.map(\.name).joined(separator: ", ")
    }

    /// Unread notes in NERVIS's notification centre, as the last status answer gave them.
    /// None count while the stack is stopping: nothing is left to read them in.
    var unread: Int { phase == .stopping || phase == .stopped ? 0 : report?.notifications?.unread ?? 0 }

    var unreadText: String { unread == 1 ? "1 unread notification" : "\(unread) unread notifications" }

    /// Starts or stops the blink to match the unread count, then draws the icon. The timer
    /// runs in the common run loop modes, so the icon keeps blinking while the menu is
    /// open — which is exactly when somebody is looking at it.
    private func updateBlinking() {
        if unread > 0, blinkTimer == nil {
            let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.blink() }
            }
            RunLoop.main.add(timer, forMode: .common)
            blinkTimer = timer
        } else if unread == 0, let timer = blinkTimer {
            timer.invalidate()
            blinkTimer = nil
            blinkTick = 0
        }
        drawIcon()
    }

    /// Six quarter-second ticks to a blink: the pupil is out on the last of them.
    private func blink() {
        blinkTick = (blinkTick + 1) % 6
        drawIcon()
    }

    private func drawIcon() {
        let closed = blinkTimer != nil && blinkTick == 5
        statusItem?.button?.image = Mark.image(whole: report?.stackIsUp ?? false, pupil: !closed)
    }

    func redraw() {
        updateBlinking()
        statusItem?.button?.toolTip = "NERVIS — \(headline)" + (unread > 0 ? " · \(unreadText)" : "")
        if menuOpen {
            menuStale = true
        } else {
            rebuildMenu()
        }
    }

    func rebuildMenu() {
        menuStale = false
        menu.removeAllItems()
        menu.addItem(NSMenuItem.sectionHeader(title: headline))
        if unread > 0 {
            menu.addItem(command(unreadText, #selector(openNotifications), key: "n", enabled: true))
        }
        menu.addItem(command("Open NERVIS dashboard", #selector(openDashboard), key: "d",
                             enabled: report != nil && phase != .stopping))
        if let report {
            menu.addItem(NSMenuItem.sectionHeader(title: "Stack"))
            addLines(for: report.stackSection)
            menu.addItem(NSMenuItem.sectionHeader(title: "Models"))
            addLines(for: report.runtimes)
            addCodex(report.codex)
        }
        let figures = Figures.lines(cpu: cpuPercent, gpu: gpuPercent, system: report?.system)
        if !figures.isEmpty {
            menu.addItem(NSMenuItem.sectionHeader(title: "This Mac"))
            figures.forEach { menu.addItem(reading($0)) }
        }
        menu.addItem(.separator())
        let stopping = phase == .stopping
        menu.addItem(command(stopping ? "Stopping the stack…" : "Quit NERVIS and stop the stack",
                             #selector(quit), key: "q", enabled: !stopping))
    }

    private func command(_ title: String, _ action: Selector, key: String, enabled: Bool) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.isEnabled = enabled
        return item
    }

    /// Each service's line — and under a service that is running but not answering, what the
    /// launcher found and what clears it, which until 13 September 2026 reached only the log.
    private func addLines(for services: [StackReport.Service]) {
        for service in services {
            menu.addItem(row(for: service))
            guard !service.answering, let problem = service.problem else { continue }
            // Quitting stops the stack through the launcher, which reaches the silent process
            // by its PID file; opening the app again starts everything. Two items rather than
            // one two-line title, so the menu grows no wider than either sentence.
            menu.addItem(reading(detail("\(service.name) is \(problem).")))
            menu.addItem(reading(detail("Quit NERVIS and open it again to restart the stack.")))
        }
    }

    /// A line of detail under a service, indented to sit under its name, in the menu's normal
    /// text colour — readable, where a disabled item's grey was not.
    private func detail(_ text: String) -> NSAttributedString {
        NSAttributedString(string: "     " + text, attributes: [.font: NSFont.menuFont(ofSize: 0)])
    }

    /// A service and whether it answers, with a green or red dot — grey for CLARVIS with no
    /// editor window open, since that is normal rather than a fault. Left enabled with no
    /// action, because a disabled item would grey the dot out and lose the one thing the
    /// row is for.
    private func row(for service: StackReport.Service) -> NSMenuItem {
        let font = NSFont.menuFont(ofSize: 0)
        let idle = service.group == "editor" ? NSColor.tertiaryLabelColor : NSColor.systemRed
        let dot = service.answering ? NSColor.systemGreen : idle
        // "not answering" when its process is there and silent, so the line agrees with the
        // detail under it; "not running" when there is no such process.
        var state = service.answering ? "running" : service.problem == nil ? "not running" : "not answering"
        if service.answering, let windows = service.windows, windows > 1 { state += " · \(windows) windows" }
        let title = NSMutableAttributedString(string: "●  ", attributes: [.foregroundColor: dot, .font: font])
        title.append(NSAttributedString(string: service.name, attributes: [.font: font]))
        title.append(NSAttributedString(
            string: "   " + state,
            attributes: [.foregroundColor: NSColor.secondaryLabelColor, .font: font]))
        let item = NSMenuItem()
        item.attributedTitle = title
        item.isEnabled = true
        // A line of the stack opens that part of it in the browser — its screen in NERVIS's
        // dashboard, or code-server itself — as the owner asked.
        if let address = service.address, let url = URL(string: address) {
            item.representedObject = url
            item.action = #selector(openAddress(_:))
            item.target = self
            item.toolTip = "Open \(service.name) in the browser"
        }
        // LM Studio's row opens a submenu: the app itself, and the installed models, each
        // loaded through SIRVIS rather than straight into LM Studio (SIRVIS.md §9).
        if service.name == "LM Studio" {
            item.submenu = lmStudioMenu()
        }
        return item
    }

    /// A machine figure. Enabled with no action, like a service line, so it is drawn in the
    /// menu's normal text colour instead of the grey a disabled item gets — grey on the menu's
    /// grey was hard to read — and so a figure can be red.
    private func reading(_ text: NSAttributedString) -> NSMenuItem {
        let item = NSMenuItem(title: text.string, action: nil, keyEquivalent: "")
        item.attributedTitle = text
        item.isEnabled = true
        return item
    }

    private func note(_ text: String) -> NSMenuItem {
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    // MARK: Codex

    /// Codex's line under the model runtimes (design §7.1), which opens the Codex card on the
    /// dashboard. Under it one line per task RAVIS lists — each opening a submenu with that
    /// task's one control, **Stop this task…** — then **Re-test the file rules…** or **Sign in to
    /// Codex…** where either applies. The allowance is the row's own words (the tightest window);
    /// the owner had the per-window lines under it removed, since they repeated the row (NERVIS
    /// 0.29.1). Nothing for a RAVIS that serves no Codex state.
    private func addCodex(_ codex: StackReport.Codex?) {
        guard let codex else { return }
        let line = CodexLine(codex)
        menu.addItem(codexRow(line, address: codex.address))
        for run in codex.runs ?? [] {
            menu.addItem(codexTaskItem(run, address: codex.address))
        }
        [codexRetestItem(codex), codexSignInItem(codex)].compactMap { $0 }.forEach { menu.addItem($0) }
    }

    /// The row itself: a dot that is never red, the words, and the Codex card behind a click.
    private func codexRow(_ line: CodexLine, address: String?) -> NSMenuItem {
        let font = NSFont.menuFont(ofSize: 0)
        let title = NSMutableAttributedString(
            string: "●  ", attributes: [.foregroundColor: line.tone.colour, .font: font])
        title.append(NSAttributedString(string: line.title, attributes: [.font: font]))
        let item = NSMenuItem()
        item.attributedTitle = title
        item.isEnabled = true
        if let address, let url = URL(string: address) {
            item.representedObject = url
            item.action = #selector(openAddress(_:))
            item.target = self
            item.toolTip = "Open the Codex card on the dashboard"
        }
        return item
    }

    private func codexTaskItem(_ run: StackReport.Codex.Run, address: String?) -> NSMenuItem {
        let text = CodexLine.taskLine(run, stopping: codexStopping)
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: text, attributes: [.font: NSFont.menuFont(ofSize: 0)])
        item.isEnabled = true
        item.indentationLevel = 1
        let submenu = NSMenu()
        submenu.autoenablesItems = false
        if let address, let url = URL(string: address) {
            let open = NSMenuItem(title: "Open the Codex card", action: #selector(openAddress(_:)), keyEquivalent: "")
            open.representedObject = url
            open.target = self
            submenu.addItem(open)
        }
        CodexLine.taskNotes(run).forEach { submenu.addItem(note($0)) }
        submenu.addItem(.separator())
        switch CodexLine.stop(run, stopping: codexStopping) {
        case .offer(let id):
            let stop = NSMenuItem(title: "Stop this task…", action: #selector(stopCodexTask(_:)), keyEquivalent: "")
            stop.representedObject = id
            stop.target = self
            stop.toolTip = "Asks you to confirm first. Stopping answers and approves nothing."
            submenu.addItem(stop)
        case .unavailable(let why):
            submenu.addItem(note(why))
        }
        item.submenu = submenu
        return item
    }

    /// **Re-test the file rules…** while an accepted build's rules are unproven — the only build
    /// RAVIS re-tests — and, for a build nobody has accepted yet, the way to the card that accepts it.
    private func codexRetestItem(_ codex: StackReport.Codex) -> NSMenuItem? {
        guard let runtime = codex.runtime, runtime.strictRules != "proven" else { return nil }
        if codexRetesting { return indented(note("Re-testing the file rules… (up to 6 minutes)")) }
        if runtime.verdict == "accepted" {
            return codexCommand("Re-test the file rules…", #selector(retestFileRules),
                                tip: "Uses one short Codex turn from your plan's allowance. Asks you first.")
        }
        guard runtime.verdict == "untested", let address = codex.address, let url = URL(string: address) else {
            return nil
        }
        let accept = codexCommand("Accept Codex \(runtime.version ?? "") on the Codex card first…",
                                  #selector(openAddress(_:)), tip: "RAVIS re-tests only a build you have accepted.")
        accept.representedObject = url
        return accept
    }

    private func codexSignInItem(_ codex: StackReport.Codex) -> NSMenuItem? {
        if codexSigningIn { return indented(note("Asking RAVIS about the sign-in…")) }
        if codex.signInWaiting == true {
            return codexCommand("Cancel the Codex sign-in", #selector(cancelCodexSignIn), tip: "Ends the sign-in page RAVIS is waiting on.")
        }
        guard CodexLine.canSignIn(codex) else { return nil }
        return codexCommand("Sign in to Codex…", #selector(signInToCodex), tip: "Opens OpenAI's sign-in page in your browser.")
    }

    private func codexCommand(_ title: String, _ action: Selector, tip: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        item.toolTip = tip
        return indented(item)
    }

    private func indented(_ item: NSMenuItem) -> NSMenuItem {
        item.indentationLevel = 1
        return item
    }

    /// Lets go of a stop once RAVIS no longer lists that task working on the turn the stop named.
    private func forgetFinishedStops() {
        guard let runs = report?.codex?.runs else { return }
        codexStopping = codexStopping.filter { id, turn in
            runs.contains { $0.id == id && $0.turnId == turn && CodexLine.stoppable.contains($0.state ?? "") }
        }
    }

    /// Stop this task…: an `NSAlert` naming the task's folder, then `run.py codex stop` with the id,
    /// folder and turn this menu showed. **Taken before the dialog opens**, so a refresh while it is
    /// open changes nothing the confirmation names; a task that moved on meanwhile is RAVIS's exit 8.
    @objc private func stopCodexTask(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String,
              let run = report?.codex?.runs?.first(where: { $0.id == id }),
              case .offer = CodexLine.stop(run, stopping: codexStopping),
              let project = run.project, let turn = run.turnId,
              confirmCodex("Stop Codex's task in \(project)?", CodexWords.stopQuestion(run), button: "Stop Task")
        else { return }
        codexStopping[id] = turn
        redraw()
        launcher.capture(["codex", "stop", id, "--project", project, "--turn", turn]) { [weak self] code, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                if code != 0 { self.codexStopping[id] = nil }
                self.refresh()
                if code != 0 {
                    self.tellCodex("Codex's task in \(project) wasn't stopped", CodexWords.stopExit(code, CodexWords.error(data)))
                }
            }
        }
    }

    /// Re-test the file rules…: the one place the re-test starts (design §3.4). It spends a short
    /// Codex turn of the plan's allowance, so it asks first, and runs in the background for up to
    /// six minutes; the result is said when it ends.
    @objc private func retestFileRules() {
        let question = CodexWords.retestQuestion(version: report?.codex?.runtime?.version)
        guard !codexRetesting, confirmCodex(question.0, question.1, button: "Re-test") else { return }
        codexRetesting = true
        redraw()
        launcher.capture(["codex", "reprove"]) { [weak self] code, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.codexRetesting = false
                self.refresh()
                let result = CodexWords.retestResult(code, CodexWords.error(data))
                self.tellCodex(result.0, result.1)
            }
        }
    }

    /// Sign in to Codex…: RAVIS starts the sign-in and the launcher opens OpenAI's page. Nothing
    /// is said when it opens; a sign-in that didn't start says why.
    @objc private func signInToCodex() {
        guard !codexSigningIn else { return }
        codexSigningIn = true
        redraw()
        launcher.capture(["codex", "sign-in"]) { [weak self] code, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.codexSigningIn = false
                self.refresh()
                if code != 0 {
                    self.tellCodex("Codex's sign-in didn't start", CodexWords.signInExit(code, CodexWords.error(data)))
                }
            }
        }
    }

    @objc private func cancelCodexSignIn() {
        guard !codexSigningIn else { return }
        codexSigningIn = true
        redraw()
        launcher.capture(["codex", "cancel-sign-in"]) { [weak self] code, data in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.codexSigningIn = false
                self.refresh()
                if code != 0 {
                    self.tellCodex("The sign-in wasn't cancelled", CodexWords.signInExit(code, CodexWords.error(data)))
                }
            }
        }
    }

    private func confirmCodex(_ title: String, _ text: String, button: String) -> Bool {
        NSApp.activate()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = text
        alert.addButton(withTitle: button)
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func tellCodex(_ title: String, _ text: String) {
        NSApp.activate()
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        alert.runModal()
    }
}

// MARK: - Checking without the menu bar

enum Preview {
    /// Every state of the mark — the stack whole, the stack not whole, and the blink —
    /// white on a dark bar and black on a light one, eight times size, so the icon can be
    /// looked at without hunting for it in the menu bar.
    static func writeIcon(to path: String) -> Bool {
        let side = 18 * 8
        let dark = NSColor(white: 0.12, alpha: 1), light = NSColor(white: 0.93, alpha: 1)
        // (ground, ink, whole, pupil)
        let looks: [(NSColor, NSColor, Bool, Bool)] = [
            (dark, .white, true, true), (dark, .white, false, true), (dark, .white, true, false),
            (light, .black, true, true), (light, .black, false, true), (light, .black, true, false),
        ]
        guard let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: side * looks.count, pixelsHigh: side, bitsPerSample: 8,
            samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
            bytesPerRow: 0, bitsPerPixel: 0),
            let context = NSGraphicsContext(bitmapImageRep: bitmap)
        else { return false }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        for (index, look) in looks.enumerated() {
            let frame = NSRect(x: index * side, y: 0, width: side, height: side)
            look.0.setFill()
            frame.fill()
            let mark = Mark.image(whole: look.2, pupil: look.3, size: CGFloat(side))
            let tinted = NSImage(size: frame.size, flipped: false) { rect in
                mark.draw(in: rect)
                look.1.set()
                rect.fill(using: .sourceAtop)
                return true
            }
            tinted.draw(in: frame)
        }
        NSGraphicsContext.restoreGraphicsState()
        guard let png = bitmap.representation(using: .png, properties: [:]) else { return false }
        return (try? png.write(to: URL(fileURLWithPath: path))) != nil
    }

    /// The menu as it would be drawn from one real status answer, printed as text — or from the
    /// status answer in `reportFile`, so a state that is hard to cause on purpose, such as a
    /// service running but not answering, can be looked at without causing it.
    @MainActor
    static func printMenu(launcher: Launcher, reportFile: String? = nil) {
        launcher.prepareNow(recordingRun: false)
        let status = reportFile.flatMap { FileManager.default.contents(atPath: $0) }
            ?? launcher.runNow(["status", "--json"], capture: true).data
        let bar = MenuBar(launcher: launcher)
        // A CPU percentage is the difference between two readings, so it waits a second.
        Thread.sleep(forTimeInterval: 1)
        bar.sampleMachine()
        bar.report = StackReport.decode(status)
        // A report from a file is not this stack's, so its models are not asked for either.
        bar.models = reportFile == nil
            ? ModelsReport.decode(launcher.runNow(["models", "--json"], capture: true).data) : nil
        bar.phase = .running
        bar.redraw()
        printItems(bar.menu.items, indent: "  ")
    }

    static func hasRed(_ text: NSAttributedString) -> Bool {
        var found = false
        text.enumerateAttribute(.foregroundColor, in: NSRange(location: 0, length: text.length)) { value, _, _ in
            if (value as? NSColor) == NSColor.systemRed { found = true }
        }
        return found
    }

    @MainActor
    private static func printItems(_ items: [NSMenuItem], indent: String) {
        for item in items {
            if item.isSeparatorItem { print("\(indent)────"); continue }
            let title = item.attributedTitle?.string ?? item.title
            let mark = item.state == .on ? "✓ " : item.state == .mixed ? "– " : ""
            let opens = item.toolTip.map { "  → \($0)" } ?? ""
            let enabled = item.isEnabled ? "" : "  (disabled)"
            let red = item.attributedTitle.map(hasRed) == true ? "  [red]" : ""
            print(item.isSectionHeader ? "\(indent)[\(title)]" : "\(indent)\(mark)\(title)\(enabled)\(red)\(opens)")
            if let submenu = item.submenu { printItems(submenu.items, indent: indent + "      ") }
        }
    }
}

// MARK: - Starting

/// The repository this app launches: NERVIS_REPOSITORY for a binary run by hand,
/// otherwise the path build_app.sh wrote into Info.plist.
func repositoryURL() -> URL? {
    let configured = ProcessInfo.processInfo.environment["NERVIS_REPOSITORY"]
        ?? Bundle.main.object(forInfoDictionaryKey: "NERVISRepository") as? String
    guard let path = configured else { return nil }
    let url = URL(fileURLWithPath: path)
    let launcher = url.appendingPathComponent("tools/run.py").path
    return FileManager.default.fileExists(atPath: launcher) ? url : nil
}

// Top-level code in a file not named main.swift is not isolated to the main actor,
// though it runs on the main thread — so it says so, and AppKit can be used from it.
MainActor.assumeIsolated {
    let arguments = CommandLine.arguments
    if let flag = arguments.firstIndex(of: "--render-icon"), arguments.indices.contains(flag + 1) {
        exit(Preview.writeIcon(to: arguments[flag + 1]) ? 0 : 1)
    }
    if arguments.contains("--preview-figures") {
        // The red threshold, checked without waiting for a busy Mac: 85 stays plain, 86 is red.
        for (cpu, gpu) in [(85, 86), (22, 100)] {
            for line in Figures.lines(cpu: cpu, gpu: gpu, system: nil) {
                print(line.string + (Preview.hasRed(line) ? "  [red]" : ""))
            }
        }
        exit(0)
    }

    guard let repository = repositoryURL() else {
        _ = NSApplication.shared
        let alert = NSAlert()
        alert.messageText = "NERVIS can't find its repository"
        alert.informativeText = "This app starts the stack from the folder it was built in, and that "
            + "folder isn't there any more. Rebuild it with nervis/packaging/macos/build_app.sh."
        alert.runModal()
        exit(1)
    }

    let launcher = Launcher(repository: repository)
    if let flag = arguments.firstIndex(of: "--print-menu") {
        // `--print-menu status.json` draws from that answer instead of asking the launcher.
        let file = arguments.indices.contains(flag + 1) ? arguments[flag + 1] : nil
        Preview.printMenu(launcher: launcher, reportFile: file)
        exit(0)
    }

    // One copy in the menu bar. Opening the app again while it runs does nothing more.
    if let identifier = Bundle.main.bundleIdentifier,
       NSRunningApplication.runningApplications(withBundleIdentifier: identifier).count > 1 {
        exit(0)
    }

    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    // The delegate property is weak; this local keeps the menu bar alive, because
    // `run()` does not return while the app is running.
    let menuBar = MenuBar(launcher: launcher)
    application.delegate = menuBar
    application.run()
}
