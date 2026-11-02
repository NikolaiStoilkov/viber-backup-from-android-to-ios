import AppKit
import SwiftUI

/// The standard Settings window (⌘,). Changes are saved as you make them.
struct SettingsView: View {
    @EnvironmentObject var model: TransferModel
    @State private var draft = EngineSettings()
    @State private var loaded = false

    var body: some View {
        Form {
            Section("Android phone") {
                PathField(title: "adb", prompt: "Detect automatically", path: $draft.adbPath)
                TextField("Phone serial", text: $draft.androidSerial, prompt: Text("First connected phone"))
                PathField(title: "Helper app (APK)", prompt: "Included with this app", path: $draft.apkPath)
            }
            Section("iPhone") {
                Toggle("Skip the iPhone step", isOn: $draft.skipIos)
                Picker("Copy the export into", selection: $draft.iosTargetApp) {
                    Text("Ask during the transfer").tag("")
                    ForEach(model.iosApps) { app in Text("\(app.name) (\(app.bundleId))").tag(app.bundleId) }
                    if !draft.iosTargetApp.isEmpty && !model.iosApps.contains(where: { $0.bundleId == draft.iosTargetApp }) {
                        Text(draft.iosTargetApp).tag(draft.iosTargetApp)
                    }
                }
                .disabled(draft.skipIos)
                HStack {
                    Button("Load Apps from iPhone") { Task { await model.loadIOSApps() } }
                        .disabled(model.loadingApps || draft.skipIos)
                    if model.loadingApps { ProgressView().controlSize(.small) }
                    if let message = model.iosAppsMessage {
                        Text(message).font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
            Section("Tools") {
                ForEach(model.status?.tools ?? []) { tool in
                    LabeledContent(tool.name) {
                        if let path = tool.path {
                            Label(path, systemImage: "checkmark.circle.fill")
                                .labelStyle(TrailingIconStyle(tint: .green))
                                .foregroundStyle(.secondary)
                        } else if let install = tool.install {
                            HStack {
                                Text(install).font(.callout.monospaced()).textSelection(.enabled)
                                Button("Copy") {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(install, forType: .string)
                                }
                                .controlSize(.small)
                            }
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 600, height: 520)
        .onAppear(perform: load)
        .onReceive(model.$status) { _ in load() }
        .onChange(of: draft) { newValue in
            if loaded && newValue != model.status?.settings { model.save(newValue) }
        }
    }

    private func load() {
        guard !loaded, let settings = model.status?.settings else { return }
        draft = settings
        loaded = true
    }
}

private struct PathField: View {
    let title: String
    let prompt: String
    @Binding var path: String

    var body: some View {
        HStack {
            TextField(title, text: $path, prompt: Text(prompt))
            Button("Choose…") {
                let panel = NSOpenPanel()
                panel.canChooseFiles = true
                panel.canChooseDirectories = false
                panel.allowsMultipleSelection = false
                if panel.runModal() == .OK, let url = panel.url { path = url.path }
            }
        }
    }
}

private struct TrailingIconStyle: LabelStyle {
    let tint: Color

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 6) {
            configuration.title
            configuration.icon.foregroundStyle(tint)
        }
    }
}
