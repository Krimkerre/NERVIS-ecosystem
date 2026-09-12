// NERVIS in the menu bar.
//
// Starts the stack in the repository this app was built from, shows whether the
// stack and the model runtimes are answering and how busy the Mac is, blinks while NERVIS has unread
// notifications, opens the dashboard in the default browser, and stops the stack
// when it quits. No Dock icon and no window: the menu bar icon is the whole of it,
// which is what the owner asked for on 12 September 2026.
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

    let services: [Service]
    let dashboard: String
    let system: System?
    let notifications: Notifications?

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

    func prepareNow() {
        environment = Launcher.loginEnvironment()
        startLog()
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

    /// A fresh log each launch, holding this session's starts, stops and status errors.
    private func startLog() {
        try? FileManager.default.createDirectory(
            at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        let stamp = ISO8601DateFormatter().string(from: Date())
        try? Data("NERVIS menu bar app started \(stamp) for \(repository.path)\n".utf8).write(to: log)
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

enum Figures {
    private static let gibibyte = 1_073_741_824.0

    /// The machine figures the menu shows, each only when it was measured. CPU and GPU come
    /// from this app's own meters, so they show even while the stack is down; memory, swap,
    /// disk and heat from NERVIS's reading, so the menu and the dashboard agree on them.
    static func lines(cpu: Int?, gpu: Int?, system: StackReport.System?) -> [String] {
        var lines: [String] = []
        if let cpu { lines.append("CPU \(cpu)%") }
        if let gpu { lines.append("GPU \(gpu)%") }
        if let total = system?.memoryTotalBytes, let available = system?.memoryAvailableBytes, total > 0 {
            let used = total - available
            lines.append("Memory \(Int((used / total * 100).rounded()))% · \(gb(used)) of \(gb(total)) GB")
        }
        var rest: [String] = []
        if let swap = system?.swapUsedBytes { rest.append("Swap \(gb(swap)) GB") }
        if let disk = system?.diskFreeBytes { rest.append(String(format: "%.0f GB disk free", disk / gibibyte)) }
        if let thermal = system?.thermalState { rest.append("thermal \(thermal)") }
        if !rest.isEmpty { lines.append(rest.joined(separator: " · ")) }
        return lines
    }

    private static func gb(_ bytes: Double) -> String { String(format: "%.1f", bytes / gibibyte) }
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
        terminate.setEventHandler { onMainRunLoop { MainActor.assumeIsolated { NSApp.terminate(nil) } } }
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
                if let report { self.report = report }
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
        timer?.invalidate()
        redraw()
        if !starting { stopThenTerminate() }
        return .terminateLater
    }

    private func stopThenTerminate() {
        launcher.run(["stop"]) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.phase = .stopped
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
    }

    @objc private func openDashboard() {
        guard let address = report?.dashboard, let url = URL(string: address) else { return }
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
            report.stackSection.forEach { menu.addItem(row(for: $0)) }
            menu.addItem(NSMenuItem.sectionHeader(title: "Models"))
            report.runtimes.forEach { menu.addItem(row(for: $0)) }
        }
        let figures = Figures.lines(cpu: cpuPercent, gpu: gpuPercent, system: report?.system)
        if !figures.isEmpty {
            menu.addItem(NSMenuItem.sectionHeader(title: "This Mac"))
            figures.forEach { menu.addItem(note($0)) }
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

    /// A service and whether it answers, with a green or red dot — grey for CLARVIS with no
    /// editor window open, since that is normal rather than a fault. Left enabled with no
    /// action, because a disabled item would grey the dot out and lose the one thing the
    /// row is for.
    private func row(for service: StackReport.Service) -> NSMenuItem {
        let font = NSFont.menuFont(ofSize: 0)
        let idle = service.group == "editor" ? NSColor.tertiaryLabelColor : NSColor.systemRed
        let dot = service.answering ? NSColor.systemGreen : idle
        var state = service.answering ? "running" : "not running"
        if service.answering, let windows = service.windows, windows > 1 { state += " · \(windows) windows" }
        let title = NSMutableAttributedString(string: "●  ", attributes: [.foregroundColor: dot, .font: font])
        title.append(NSAttributedString(string: service.name, attributes: [.font: font]))
        title.append(NSAttributedString(
            string: "   " + state,
            attributes: [.foregroundColor: NSColor.secondaryLabelColor, .font: font]))
        let item = NSMenuItem()
        item.attributedTitle = title
        item.isEnabled = true
        // LM Studio's row opens a submenu: the app itself, and the installed models, each
        // loaded through SIRVIS rather than straight into LM Studio (SIRVIS.md §9).
        if service.name == "LM Studio" {
            item.submenu = lmStudioMenu()
        }
        return item
    }

    private func note(_ text: String) -> NSMenuItem {
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
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

    /// The menu as it would be drawn from one real status answer, printed as text.
    @MainActor
    static func printMenu(launcher: Launcher) {
        launcher.prepareNow()
        let result = launcher.runNow(["status", "--json"], capture: true)
        let bar = MenuBar(launcher: launcher)
        // A CPU percentage is the difference between two readings, so it waits a second.
        Thread.sleep(forTimeInterval: 1)
        bar.sampleMachine()
        bar.report = StackReport.decode(result.data)
        bar.models = ModelsReport.decode(launcher.runNow(["models", "--json"], capture: true).data)
        bar.phase = .running
        bar.redraw()
        printItems(bar.menu.items, indent: "  ")
    }

    @MainActor
    private static func printItems(_ items: [NSMenuItem], indent: String) {
        for item in items {
            if item.isSeparatorItem { print("\(indent)────"); continue }
            let title = item.attributedTitle?.string ?? item.title
            let mark = item.state == .on ? "✓ " : item.state == .mixed ? "– " : ""
            let opens = item.toolTip.map { "  → \($0)" } ?? ""
            let enabled = item.isEnabled ? "" : "  (disabled)"
            print(item.isSectionHeader ? "\(indent)[\(title)]" : "\(indent)\(mark)\(title)\(enabled)\(opens)")
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
    if arguments.contains("--print-menu") {
        Preview.printMenu(launcher: launcher)
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
