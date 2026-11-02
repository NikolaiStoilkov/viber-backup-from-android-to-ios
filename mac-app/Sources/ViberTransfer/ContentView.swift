import AppKit
import SwiftUI

/// The main window: device cards, Start/Cancel, prompts, steps, summary and log.
struct ContentView: View {
    @EnvironmentObject var model: TransferModel
    @State private var confirmCancel = false
    @State private var confirmUninstall = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                if let error = model.engineError {
                    Banner(symbol: "exclamationmark.triangle.fill", tint: .red, text: error)
                }
                devices
                controls
                if model.running, let prompt = model.job?.prompt {
                    Banner(symbol: "hand.point.up.left.fill", tint: .orange, text: prompt)
                }
                progress
                if let summary = model.job?.summary {
                    SummaryView(summary: summary, onReveal: model.revealBackup,
                                onUninstall: { confirmUninstall = true })
                }
                LogView(lines: model.log)
            }
            .padding(24)
        }
        .frame(minWidth: 760, minHeight: 700)
        .sheet(item: Binding(get: { model.running ? model.job?.choice : nil }, set: { _ in })) { choice in
            ChoiceSheet(choice: choice) { model.answer($0) }
        }
        .confirmationDialog("Stop the transfer?", isPresented: $confirmCancel) {
            Button("Stop Transfer", role: .destructive) { model.cancel() }
            Button("Keep Going", role: .cancel) {}
        } message: {
            Text("Temporary files on the phone and the Mac are removed. Viber's own data is never changed.")
        }
        .confirmationDialog("Remove the helper app from the Android phone?", isPresented: $confirmUninstall) {
            Button("Remove Helper App", role: .destructive) { model.uninstallHelper() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This uninstalls only “Viber Transfer Helper”. Viber and your chats are not touched.")
        }
        .alert("Viber Transfer", isPresented: Binding(get: { model.alertMessage != nil },
                                                      set: { if !$0 { model.alertMessage = nil } })) {
            Button("OK") {}
        } message: {
            Text(model.alertMessage ?? "")
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Viber Transfer").font(.largeTitle.weight(.semibold))
                Text("Back up Viber from your Android phone to this Mac, then copy a readable export to your iPhone.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            SettingsButton()
        }
    }

    private var devices: some View {
        HStack(alignment: .center, spacing: 14) {
            DeviceCard(title: "Android (source)", symbol: "candybarphone", status: model.status?.android,
                       detail: [model.status?.android.model, model.status?.android.viber.map { "Viber \($0)" }])
            Image(systemName: "arrow.right.circle.fill")
                .font(.system(size: 28))
                .foregroundStyle(Color.accentColor)
            DeviceCard(title: "iPhone (target)", symbol: "iphone", status: model.status?.iphone,
                       detail: [model.status?.iphone.name, model.status?.iphone.iosVersion.map { "iOS \($0)" }])
        }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Button {
                model.start()
            } label: {
                Label("Start Transfer", systemImage: "arrow.left.arrow.right")
                    .padding(.horizontal, 6)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .disabled(model.running || model.engineError != nil)

            Button("Cancel") { confirmCancel = true }
                .controlSize(.large)
                .disabled(!model.running)

            if let apk = model.status?.apk, !apk.exists {
                Label("The helper app isn't included, so the root copy is unavailable.",
                      systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var progress: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                StepGrid(steps: model.steps, job: model.job)
                if model.running {
                    if let value = model.job?.progress {
                        ProgressView(value: value, total: 100)
                    } else {
                        ProgressView().progressViewStyle(.linear)
                    }
                } else if model.job?.result == "success" {
                    ProgressView(value: 1)
                }
                ResultLine(job: model.job)
            }
            .padding(8)
        }
    }
}

// MARK: - Pieces

struct Banner: View {
    let symbol: String
    let tint: Color
    let text: String

    var body: some View {
        Label {
            Text(text).font(.title3.weight(.medium)).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: symbol).foregroundStyle(tint)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(tint.opacity(0.35)))
    }
}

struct StatusBadge: View {
    let status: DeviceStatus?

    private var tint: Color {
        switch status?.state {
        case "connected": return .green
        case "no_root", "unauthorized", "not_trusted": return .orange
        case "not_connected", nil: return .secondary
        default: return .red
        }
    }

    var body: some View {
        Text(status?.label ?? "Checking…")
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(tint)
            .background(tint.opacity(0.15), in: Capsule())
    }
}

struct DeviceCard: View {
    let title: String
    let symbol: String
    let status: DeviceStatus?
    let detail: [String?]

    var body: some View {
        GroupBox {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: symbol)
                    .font(.system(size: 38, weight: .light))
                    .foregroundStyle(.secondary)
                    .frame(width: 44)
                VStack(alignment: .leading, spacing: 6) {
                    Text(title.uppercased()).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                    StatusBadge(status: status)
                    let text = detail.compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
                    if !text.isEmpty { Text(text).font(.headline) }
                    Text(status?.message ?? " ")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                Spacer(minLength: 0)
            }
            .padding(8)
            .frame(maxWidth: .infinity, minHeight: 110, alignment: .topLeading)
        }
    }
}

struct StepGrid: View {
    let steps: [String]
    let job: JobState?

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), alignment: .leading)], alignment: .leading,
                  spacing: 8) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, name in
                let look = appearance(index: index, name: name)
                Label {
                    Text(name).foregroundStyle(look.text)
                        .fontWeight(look.symbol == "arrow.right.circle.fill" ? .semibold : .regular)
                } icon: {
                    Image(systemName: look.symbol).foregroundStyle(look.tint)
                }
            }
        }
    }

    private func appearance(index: Int, name: String) -> (symbol: String, tint: Color, text: Color) {
        let current = job?.stepIndex ?? -1
        if job?.skipped?.contains(name) == true { return ("minus.circle", .secondary, .secondary) }
        if index < current || (index == current && job?.result == "success") {
            return ("checkmark.circle.fill", .green, .primary)
        }
        if index == current && job?.running == true { return ("arrow.right.circle.fill", .accentColor, .primary) }
        if index == current && job?.result == "failed" { return ("xmark.circle.fill", .red, .primary) }
        return ("circle", .secondary.opacity(0.6), .secondary)
    }
}

struct ResultLine: View {
    let job: JobState?

    var body: some View {
        switch job?.result {
        case "success":
            Label("Transfer finished.", systemImage: "checkmark.seal.fill").foregroundStyle(.green).font(.headline)
        case "failed":
            Label("Stopped: \(job?.error ?? "")", systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red).font(.headline).textSelection(.enabled)
        case "cancelled":
            Label("Cancelled. Temporary files were cleaned up.", systemImage: "stop.circle.fill")
                .foregroundStyle(.orange).font(.headline)
        default:
            if let step = job?.step, job?.running == true {
                Text(step + "…").foregroundStyle(.secondary)
            }
        }
    }
}

struct SummaryView: View {
    let summary: Summary
    let onReveal: () -> Void
    let onUninstall: () -> Void

    private var rows: [(String, String)] {
        var rows: [(String, String)] = [
            ("Saved to", summary.backupDir),
            ("Method", summary.method == "root" ? "Root copy via helper app" : "adb backup"),
            ("Source phone", summary.device),
            ("Viber version", summary.viberVersion ?? "—"),
            ("Total", "\(files(summary.files)), \(summary.size)"),
        ]
        for (name, folder) in summary.folders.sorted(by: { $0.key < $1.key }) {
            rows.append((name, "\(files(folder.files)), \(folder.size)"))
        }
        rows.append(("Conversations", summary.conversionError != nil
            ? "Conversion failed – see the log"
            : "\(summary.conversations ?? 0) (\(summary.messages ?? 0) messages)"))
        if let n = summary.verified.verifiedFiles {
            rows.append(("Checksums", "All \(n) files match the phone's checksums"))
        } else if summary.verified.abSha256 != nil {
            rows.append(("Checksums", "Backup archive unpacked cleanly; hash recorded in manifest.json"))
        }
        rows.append(("iPhone", "\(summary.ios.status.capitalized): \(summary.ios.detail)"))
        return rows
    }

    var body: some View {
        GroupBox("Summary") {
            VStack(alignment: .leading, spacing: 12) {
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 18, verticalSpacing: 7) {
                    ForEach(rows, id: \.0) { label, value in
                        GridRow {
                            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
                            Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                HStack {
                    Button("Show in Finder", action: onReveal)
                    Button("Remove Helper App from Phone…", role: .destructive, action: onUninstall)
                }
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func files(_ n: Int) -> String { n == 1 ? "1 file" : "\(n) files" }
}

struct LogView: View {
    let lines: [String]

    var body: some View {
        GroupBox {
            ScrollViewReader { proxy in
                ScrollView {
                    Text(lines.isEmpty ? "The log appears here when a transfer starts." : lines.joined(separator: "\n"))
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(lines.isEmpty ? .secondary : .primary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(6)
                    Color.clear.frame(height: 1).id("end")
                }
                .frame(height: 210)
                .onChange(of: lines.count) { _ in proxy.scrollTo("end", anchor: .bottom) }
            }
        } label: {
            HStack {
                Text("Log")
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(lines.joined(separator: "\n"), forType: .string)
                }
                .disabled(lines.isEmpty)
                Button("Open Log File") { NSWorkspace.shared.open(Engine.logURL) }
            }
            .controlSize(.small)
        }
    }
}

struct ChoiceSheet: View {
    let choice: Choice
    let onAnswer: (String) -> Void
    @State private var selection: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Choose where to copy", systemImage: "iphone").font(.title2.weight(.semibold))
            Text(choice.question).fixedSize(horizontal: false, vertical: true)
            Picker("", selection: $selection) {
                ForEach(choice.options, id: \.value) { Text($0.label).tag($0.value) }
            }
            .pickerStyle(.radioGroup)
            .labelsHidden()
            HStack {
                Spacer()
                Button("Continue") { onAnswer(selection) }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 480)
        .interactiveDismissDisabled()
        .onAppear { selection = choice.options.first?.value ?? "" }
    }
}

/// Opens the Settings window (SettingsLink exists only on macOS 14+).
struct SettingsButton: View {
    var body: some View {
        if #available(macOS 14, *) {
            SettingsLink { Label("Settings", systemImage: "gearshape") }
        } else {
            Button {
                NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
            } label: {
                Label("Settings", systemImage: "gearshape")
            }
        }
    }
}
