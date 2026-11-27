import AppKit
import CardboardCADKit

/// A layout check of the real main window, for CI (`scripts/ci/window_probe.sh`).
///
/// Off unless the app is launched with CARDBOARD_WINDOW_PROBE=<folder>. Then the
/// app uses a throwaway in-memory API key (so the chat's message box is shown;
/// nothing is ever sent to Claude), fills the chat with a long conversation,
/// sizes the window to a few sizes, and for each one writes:
///   probe-<w>x<h>.json   where AppKit actually placed the message box, in window
///                        coordinates, plus the whole view tree with frames
///   probe-<w>x<h>.ready  marker: the script screenshots the window now
/// then it quits. The script checks the JSON and keeps the screenshots.
@MainActor
enum WindowProbe {
    static let environmentKey = "CARDBOARD_WINDOW_PROBE"
    /// Fake key for probe runs only. It never leaves the app: the probe sends no message.
    static let placeholderKey = "sk-ant-probe-0000000000000000000000000000"
    /// Height of the status bar at the bottom of the window (28 + its divider).
    static let statusBarHeight: CGFloat = 29

    static var folder: URL? {
        guard let path = ProcessInfo.processInfo.environment[environmentKey], !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    static var isEnabled: Bool { folder != nil }

    static let sizes: [NSSize] = [
        NSSize(width: 1280, height: 800),
        NSSize(width: 1110, height: 604),   // the size in the bug report
        NSSize(width: 900, height: 480),    // the smallest window
    ]

    static func run(engine: EngineManager, assistant: DesignAssistant) async {
        guard let folder else { return }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        for _ in 0..<240 {
            if case .running = engine.state { break }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        // A short chat (no scrolling, as in the bug report) and a long one.
        for (chat, rounds) in [("short", 1), ("long", 3)] {
            fillChat(assistant, rounds: rounds)
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            for size in sizes {
                guard let window = mainWindow() else {
                    write(["error": "no main window"], to: folder.appendingPathComponent("probe-error.json"))
                    break
                }
                window.setContentSize(size)
                window.center()
                window.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                window.contentView?.layoutSubtreeIfNeeded()
                let name = "probe-\(chat)-\(Int(size.width))x\(Int(size.height))"
                write(measure(window, requested: size), to: folder.appendingPathComponent("\(name).json"))
                writeCachedImage(window, to: folder.appendingPathComponent("\(name)-cache.png"))
                FileManager.default.createFile(atPath: folder.appendingPathComponent("\(name).ready").path,
                                               contents: Data("\(window.windowNumber)\n".utf8))
                // Time for the script to take a screenshot of this size.
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        }
        // Quit the way Cmd+Q does: through the menu item that owns the shortcut.
        let quit = quitMenuItem()
        write(["found": quit != nil, "title": quit?.title ?? ""],
              to: folder.appendingPathComponent("quit-menu.json"))
        FileManager.default.createFile(atPath: folder.appendingPathComponent("done").path, contents: Data())
        // From the run loop, as a key press would be, not from inside this task:
        // termination waits on main-actor work (stopping the engine), which
        // cannot run while this task holds the main actor.
        DispatchQueue.main.async {
            if let quit, let action = quit.action {
                NSApp.sendAction(action, to: quit.target, from: quit)
            } else {
                NSApp.terminate(nil)
            }
        }
    }

    /// The enabled menu item whose shortcut is Cmd+Q.
    static func quitMenuItem() -> NSMenuItem? {
        func find(_ menu: NSMenu) -> NSMenuItem? {
            for item in menu.items {
                if item.keyEquivalent == "q", item.keyEquivalentModifierMask.intersection(.deviceIndependentFlagsMask) == .command,
                   item.isEnabled, item.action != nil {
                    return item
                }
                if let sub = item.submenu, let hit = find(sub) { return hit }
            }
            return nil
        }
        return NSApp.mainMenu.flatMap(find)
    }

    /// A chat like a real one: the designer's request, tool rows, and Claude's
    /// replies with bold text and "- " lists.
    private static func fillChat(_ assistant: DesignAssistant, rounds: Int) {
        let reply = """
        I made the stool: a **350 mm wide, 450 mm tall** cylinder, sliced into a waffle for 6 mm board.

        - **Parts:** 10 ribs, 5 across X and 5 across Y. The ribs that cross X are slotted from the top.
        - **Sheets:** the parts fit on 2 sheets of 1200 x 800 mm, using about 68% of the board.

        You can change the number of ribs or the sheet size. I can also do a cost estimate, or export the cut files.
        """
        var items: [JSONValue] = []
        for _ in 0..<rounds {
            items.append(["kind": "user", "text": "Make a cylinder stool 450 mm tall and 350 mm wide as a waffle from 6 mm board"])
            items.append(["kind": "tool", "tool": "create_shape", "is_error": false,
                          "text": "Created cylinder: 350 x 350 x 450 mm (W x D x H), 96 faces"])
            items.append(["kind": "tool", "tool": "waffle_slice", "is_error": false,
                          "text": "Waffle: 10 parts (X1, X2, X3, X4, X5, Y1, Y2, Y3, Y4, Y5). Largest part 350 x 450 mm."])
            items.append(["kind": "assistant", "text": .string(reply)])
        }
        let chat: JSONValue = ["schema": .string(DesignAssistant.conversationSchema),
                               "items": .array(items), "history": []]
        if !assistant.restore(conversation: chat) { assistant.addNotice(reply) }
    }

    static func mainWindow() -> NSWindow? {
        NSApp.windows.first { $0.isVisible && !($0 is NSPanel) && $0.title == AppInfo.name }
            ?? NSApp.windows.first { $0.isVisible && !($0 is NSPanel) && $0.contentView != nil }
    }

    // MARK: measuring

    static func measure(_ window: NSWindow, requested: NSSize) -> [String: Any] {
        let layout = window.contentLayoutRect
        var fields: [[String: Any]] = []
        var tree: [String] = []
        if let root = window.contentView {
            walk(root, depth: 0, fields: &fields, tree: &tree)
        }
        // The message box: the lowest editable text view in the left half.
        let candidates = fields.filter { ($0["editable"] as? Bool) == true && !(($0["hidden"] as? Bool) ?? false) }
        let composer = candidates
            .filter { (($0["x"] as? Double) ?? 0) < layout.midX }
            .min { (($0["y"] as? Double) ?? 0) < (($1["y"] as? Double) ?? 0) }

        var problems: [String] = []
        if let c = composer {
            let y = c["y"] as? Double ?? 0, h = c["h"] as? Double ?? 0
            let visibleH = c["visibleH"] as? Double ?? 0
            if y < Double(layout.minY + statusBarHeight) - 0.5 {
                problems.append("message box bottom (y=\(y)) is below the status bar top (y=\(Double(layout.minY + statusBarHeight)))")
            }
            if y + h > Double(layout.maxY) + 0.5 {
                problems.append("message box top (y=\(y + h)) is above the window content (\(Double(layout.maxY)))")
            }
            if visibleH < h - 0.5 {
                problems.append("message box is clipped: \(visibleH) of \(h) pt visible")
            }
        } else {
            problems.append("no message box found")
        }
        return [
            "requested": ["w": requested.width, "h": requested.height],
            "contentLayoutRect": rect(layout),
            "windowFrame": rect(window.frame),
            "statusBarTop": Double(layout.minY + statusBarHeight),
            "composer": composer ?? NSNull(),
            "editableFields": candidates,
            "problems": problems,
            "ok": problems.isEmpty,
            "tree": tree,
        ]
    }

    private static func walk(_ view: NSView, depth: Int, fields: inout [[String: Any]], tree: inout [String]) {
        let inWindow = view.convert(view.bounds, to: nil)
        let visible = view.visibleRect
        let type = String(describing: Swift.type(of: view))
        if depth < 40 {
            let pad = String(repeating: "  ", count: depth)
            tree.append("\(pad)\(type) x=\(Int(inWindow.minX)) y=\(Int(inWindow.minY)) w=\(Int(inWindow.width)) h=\(Int(inWindow.height))"
                        + " visibleH=\(Int(visible.height))\(view.isHidden ? " hidden" : "")")
        }
        let editable = (view as? NSTextView)?.isEditable ?? ((view as? NSTextField)?.isEditable ?? false)
        if view is NSTextView || view is NSTextField {
            fields.append([
                "type": type,
                "editable": editable,
                "hidden": view.isHiddenOrHasHiddenAncestor,
                "x": Double(inWindow.minX), "y": Double(inWindow.minY),
                "w": Double(inWindow.width), "h": Double(inWindow.height),
                "visibleH": Double(visible.height),
            ])
        }
        for sub in view.subviews {
            walk(sub, depth: depth + 1, fields: &fields, tree: &tree)
        }
    }

    /// AppKit's own drawing of the window content, in case `screencapture` is not allowed.
    private static func writeCachedImage(_ window: NSWindow, to url: URL) {
        guard let view = window.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }

    private static func rect(_ r: NSRect) -> [String: Double] {
        ["x": Double(r.minX), "y": Double(r.minY), "w": Double(r.width), "h": Double(r.height)]
    }

    private static func write(_ object: [String: Any], to url: URL) {
        if let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: url)
        }
    }
}
