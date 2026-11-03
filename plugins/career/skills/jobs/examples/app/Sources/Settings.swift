import SwiftUI

struct RoleSettings: Decodable {
    var drafter: Choice?
    var reviewer: Choice?
    struct Choice: Decodable { var agent: String;var model: String? }
}
struct ModelOption: Decodable, Identifiable {
    var agent: String;var model: String;var label: String
    var id: String { agent+":"+model }
}
struct ModelCatalog: Decodable {
    var models: [ModelOption]
    var errors: [DiscoveryError]
    struct DiscoveryError: Decodable { var agent: String;var error: String }
}
private struct RoleModelPicker: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let role: String
    @State private var catalog: ModelCatalog?
    @State private var error: String?
    var body: some View {
        NavigationStack {
            List {
                if let catalog {
                    ForEach(catalog.models) { choice in
                        Button(choice.agent.capitalized+" / "+choice.label) { Task { if await store.saveRole(role,choice) { dismiss() } } }
                    }
                    ForEach(catalog.errors,id:\.agent) { issue in Text(issue.agent+": "+issue.error).foregroundStyle(.orange) }
                } else if let error { Text(error).foregroundStyle(.orange) }
                else { ProgressView("Loading installed CLI models…") }
            }.chatButton().navigationTitle("Choose "+role)
                .toolbar { Button("Cancel") { dismiss() } }
                .task { do { catalog = try await store.modelChoices() } catch { self.error = error.localizedDescription } }
        }
    }
}

struct CareerSettingsSections: View {
    @Environment(AppStore.self) private var store
    @State private var choosingRole: String?
    var body: some View {
        Group { rolesSection; notificationsSection }
            .sheet(isPresented:Binding(get:{choosingRole != nil},set:{if !$0 { choosingRole = nil }})) {
                if let role = choosingRole { RoleModelPicker(role:role) }
            }
    }
    private var rolesSection: some View {
        Section("Writing and review") {
            roleButton("drafter")
            roleButton("reviewer")
        }
    }
    private func roleButton(_ role: String) -> some View {
        let choice: RoleSettings.Choice? = role == "drafter" ? store.roleSettings?.drafter : store.roleSettings?.reviewer
        let detail: String
        if let choice { detail = choice.agent+" / "+(choice.model ?? "choose a model") }
        else { detail = "choose an agent and model" }
        return Button { choosingRole = role } label: {
            VStack(alignment:.leading) {
                Text(role.capitalized+": "+detail)
                Text(role == "drafter" ? "Recommended: Claude / Opus" : "Recommended: Claude / Sonnet")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    private var notificationText: String {
        guard let status = store.notificationStatus, status.choice != nil else { return "Would you like WhatsApp notifications for this search?" }
        return status.enabled ? "Enabled for the dedicated career group" : "Off; everything stays in the app"
    }
    private var notificationsSection: some View {
        Section("WhatsApp notifications") {
            Text(notificationText)
            ForEach(store.notificationIssues,id:\.self) { Text($0).foregroundStyle(.orange) }
            HStack {
                Button("Yes") { Task { await store.setNotifications(true) } }
                Button("No") { Task { await store.setNotifications(false) } }
            }
        }
    }
}

struct NotificationStatus: Decodable {
    var enabled: Bool
    var choice: String?
    var group: Group?
    var issues: [String]
    var messages: [Notice]
    struct Notice: Decodable { var message: String;var kind: String?;var state: String }
    struct Group: Decodable { var id: String; var name: String }
}

struct SavedAnswer: Decodable, Identifiable {
    var id: String
    var name: String?
    var scope: String?
    var value: String
}

struct SavedAnswersView: View {
    @Environment(AppStore.self) private var store
    @State private var answers: [SavedAnswer] = []
    @State private var error: String?
    var body: some View {
        List {
            if let error { Text(error).foregroundStyle(.red) }
            ForEach($answers) { $answer in
                Section(answer.name ?? "Saved answer") {
                    Text(answer.scope ?? "Earlier saved answer").font(.caption)
                    TextField("Answer", text: $answer.value)
                    Button("Ask me every time") { answer.value = "Ask me every time" }
                    Button("Save answer") {
                        Task {
                            do { try await store.changeSavedAnswer(answer.id, value: answer.value); error = nil }
                            catch { self.error = error.localizedDescription }
                        }
                    }
                }
            }
        }
        .chatButton()
        .navigationTitle("Saved answers")
        .task {
            do { answers = try await store.savedAnswers() }
            catch { self.error = error.localizedDescription }
        }
    }
}
