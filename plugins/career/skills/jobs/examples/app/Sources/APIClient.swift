import CryptoKit
import Foundation
import ImageIO
import UIKit

enum APIError: LocalizedError {
    case notPaired
    case unauthorized
    /// 429: too many failed sign-ins from this network; nothing is sent until `until`.
    case rateLimited(until: Date)
    case http(Int, String)
    case transport(String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .notPaired: "Not paired with the server yet."
        case .unauthorized: "Token rejected (401). Re-pair with the server."
        case .rateLimited(let until):
            "Too many failed sign-ins from this network. Try again in \(AuthGate.remainingText(until))."
        case .http(let code, let msg): "Server said \(code): \(msg)"
        case .transport(let msg): "Could not reach the server: \(msg)"
        case .decoding(let msg): "Unexpected reply: \(msg)"
        }
    }
}

/// Closure-based client (see apple-dev-docs guides/swiftui/components/lightweight-clients.md):
/// URL building and decoding live here, state lives in AppStore.
struct APIClient {
    var health: @Sendable () async throws -> Health
    var stamp: @Sendable () async throws -> DataStamp
    var list: @Sendable () async throws -> [Posting]
    var get: @Sendable (_ id: String) async throws -> Posting
    /// `GET /batch`: the Today batch; a server without the endpoint (404) reads as no batch.
    var batch: @Sendable () async throws -> Batch
    var rule: @Sendable (_ id: String, _ decision: Decision, _ reason: String?) async throws -> Posting
    var applied: @Sendable (_ id: String) async throws -> Posting
    /// `GET /postings/<id>/fill`: the Apply stage's form_fill.json, or nil when apply has not run (404).
    var fill: @Sendable (_ id: String) async throws -> FormFill?
    /// `GET /postings/<id>/approve-submit`: dry check of the submit gate.
    var submitGate: @Sendable (_ id: String) async throws -> SubmitGate
    /// `POST /postings/<id>/approve-submit`: stamps ruling.submit_approved_at; 409 lists what is missing.
    var approveSubmit: @Sendable (_ id: String, _ note: String?, _ fillDigest: String) async throws -> Posting
    /// `GET /postings/<id>/preview`: the Application preview (every field as the employer receives it, the gate).
    var preview: @Sendable (_ id: String) async throws -> PreviewDoc
    /// `GET /postings/<id>/review`: the per-box review, or nil before `career.py fill` (404).
    var review: @Sendable (_ id: String) async throws -> ReviewDoc?
    /// `POST /postings/<id>/review/<box>/approve`; `seen` is the box's value_changed_at the user looked at (409 if it moved).
    var approveBox: @Sendable (_ id: String, _ key: String, _ seen: String?) async throws -> ReviewDoc
    /// `POST /postings/<id>/review/<box>/edit`: "Use my text". Saved as his own; the session refills the form.
    var editBox: @Sendable (_ id: String, _ key: String, _ text: String) async throws -> ReviewDoc
    /// `POST /postings/<id>/review/<box>/comment`: the comment goes to the coordinating agent session.
    var commentBox: @Sendable (_ id: String, _ key: String, _ text: String) async throws -> ReviewDoc
    /// Raw bytes of `/files/<relPath>`, never cached (form screenshots are overwritten in place on re-fill).
    var fetchData: @Sendable (_ relPath: String) async throws -> Data
    /// Downloads `/files/<relPath>` to a temp file and returns its URL (for QuickLook).
    var download: @Sendable (_ relPath: String) async throws -> URL
    /// Loads an image from `/files/<relPath>`, downsampled so its longest side is at most `maxPixel`.
    /// Cached in memory (decoded, per size) and on disk (raw bytes, temp dir) so rows and pagers reuse it.
    var fetchImage: @Sendable (_ relPath: String, _ maxPixel: Int) async throws -> UIImage
    /// `GET /questions`: every question, open first (workspace/data/questions).
    var questions: @Sendable () async throws -> [Question]
    /// `POST /questions/<id>/answer`: the server records the answer and applies it at once.
    /// `note` is an optional comment; a nil answer with a note sends the comment only (the question stays open).
    var answerQuestion: @Sendable (_ id: String, _ answer: String?, _ note: String?) async throws -> Question
    /// `POST /questions/<id>/dismiss`.
    var dismissQuestion: @Sendable (_ id: String) async throws -> Question
    /// `GET /chat?since=N`: the support chat after message N (0 = all of it).
    var savedAnswers: @Sendable () async throws -> [SavedAnswer]
    var changeSavedAnswer: @Sendable (_ id: String, _ value: String) async throws -> SavedAnswer
    var notifications: @Sendable () async throws -> NotificationStatus
    var setNotifications: @Sendable (Bool) async throws -> NotificationStatus
    var roleSettings: @Sendable () async throws -> RoleSettings
    var modelChoices: @Sendable () async throws -> ModelCatalog
    var saveRole: @Sendable (String,ModelOption) async throws -> Void
    var chat: @Sendable (_ since: Int) async throws -> ChatPage
    /// `POST /chat`: the server stores the message, posts it to the your agent session and copies it to WhatsApp.
    /// With `postingId` it is a comment on that application, which holds it until the session resolves it.
    var sendChat: @Sendable (_ text: String, _ postingId: String?) async throws -> ChatMessage
}

/// Stops a stale token from locking the user's network out. The server allows 20 failed sign-ins per client IP per
/// 10 minutes, then answers 429. After ONE 401 this gate refuses every further token-checked request (images too)
/// until the phone pairs again; after a 429 it refuses until Retry-After has passed. Both states persist across
/// launches, keyed by a fingerprint of host + token, so relaunching cannot spend more failures.
final class AuthGate: @unchecked Sendable {
    enum State: Equatable { case open, unauthorized, rateLimited(until: Date) }

    private let lock = NSLock()
    private var _state: State
    private let key: String?
    private let onChange: @Sendable (State) -> Void

    /// `persistFor`: the pairing whose state is remembered across launches (nil for a throwaway probe client).
    init(persistFor pairing: Pairing? = nil, onChange: @escaping @Sendable (State) -> Void = { _ in }) {
        self.onChange = onChange
        key = pairing.map { Self.fingerprint($0) }
        _state = .open
        if let key, let saved = UserDefaults.standard.dictionary(forKey: Self.defaultsKey), saved["key"] as? String == key {
            if saved["state"] as? String == "unauthorized" {
                _state = .unauthorized
            } else if let until = saved["until"] as? Date, until > .now {
                _state = .rateLimited(until: until)
            }
        }
    }

    var state: State {
        lock.lock(); defer { lock.unlock() }
        if case .rateLimited(let until) = _state, until <= .now { _state = .open }
        return _state
    }

    /// Throws instead of sending when the gate is shut.
    func check() throws {
        switch state {
        case .open: return
        case .unauthorized: throw APIError.unauthorized
        case .rateLimited(let until): throw APIError.rateLimited(until: until)
        }
    }

    func trip(_ new: State) {
        lock.lock()
        let changed = _state != new
        _state = new
        lock.unlock()
        guard changed else { return }
        if let key {
            switch new {
            case .open: UserDefaults.standard.removeObject(forKey: Self.defaultsKey)
            case .unauthorized: UserDefaults.standard.set(["key": key, "state": "unauthorized"], forKey: Self.defaultsKey)
            case .rateLimited(let until):
                UserDefaults.standard.set(["key": key, "state": "rateLimited", "until": until], forKey: Self.defaultsKey)
            }
        }
        onChange(new)
    }

    /// A new pairing starts clean.
    static func forget() { UserDefaults.standard.removeObject(forKey: defaultsKey) }

    static func remainingText(_ until: Date, now: Date = .now) -> String {
        let s = max(0, Int(until.timeIntervalSince(now).rounded(.up)))
        return s >= 60 ? "\(s / 60) min \(s % 60) s" : "\(s) s"
    }

    private static let defaultsKey = "auth.gate"

    private static func fingerprint(_ p: Pairing) -> String {
        let digest = SHA256.hash(data: Data((p.host + "\n" + p.token).utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }
}

extension APIClient {
    private static func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try JSONDecoder.career.decode(T.self, from: data) }
        catch { throw APIError.decoding(error.localizedDescription) }
    }

    static func live(_ pairing: Pairing, gate: AuthGate = AuthGate()) -> APIClient {
        let base = pairing.baseURL
        let token = pairing.token
        let session: URLSession = {
            let c = URLSessionConfiguration.default
            c.timeoutIntervalForRequest = 15
            c.waitsForConnectivity = false
            return URLSession(configuration: c)
        }()

        @Sendable func request(_ path: String, query: [URLQueryItem] = [], method: String = "GET", body: [String: Any?]? = nil) throws -> URLRequest {
            guard let base else { throw APIError.notPaired }
            var comps = URLComponents(url: base.appending(path: path), resolvingAgainstBaseURL: false)!
            if !query.isEmpty { comps.queryItems = query }
            var r = URLRequest(url: comps.url!)
            r.httpMethod = method
            r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            if let body {
                r.setValue("application/json", forHTTPHeaderField: "Content-Type")
                r.httpBody = try JSONSerialization.data(withJSONObject: body.compactMapValues { $0 })
            }
            return r
        }

        @Sendable func send(_ r: URLRequest) async throws -> Data {
            try gate.check()                      // a shut gate sends nothing: no more failed sign-ins
            let (data, resp): (Data, URLResponse)
            do { (data, resp) = try await session.data(for: r) }
            catch { throw APIError.transport(error.localizedDescription) }
            let http = resp as? HTTPURLResponse
            let code = http?.statusCode ?? 0
            switch code {
            case 200..<300: return data
            case 401:
                gate.trip(.unauthorized)
                throw APIError.unauthorized
            case 429:
                let wait = Double(http?.value(forHTTPHeaderField: "Retry-After") ?? "") ?? 600
                let until = Date.now.addingTimeInterval(max(1, wait))
                gate.trip(.rateLimited(until: until))
                throw APIError.rateLimited(until: until)
            default:
                let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                throw APIError.http(code, msg ?? String(decoding: data.prefix(200), as: UTF8.self))
            }
        }

        let images = ImageCache()

        return APIClient(
            health: { try decode(Health.self, try await send(try request("health"))) },
            stamp: { try decode(DataStamp.self, try await send(try request("stamp"))) },
            list: { try decode([Posting].self, try await send(try request("postings"))) },
            get: { id in try decode(Posting.self, try await send(try request("postings/\(id)"))) },
            batch: {
                do { return try decode(Batch.self, try await send(try request("batch"))) }
                catch APIError.http(404, _) { return .empty }
            },
            rule: { id, decision, reason in
                let body: [String: Any?] = ["decision": decision.rawValue, "reason": reason, "by": "phone"]
                return try decode(Posting.self, try await send(try request("postings/\(id)/ruling", method: "POST", body: body)))
            },
            applied: { id in
                try decode(Posting.self, try await send(try request("postings/\(id)/applied", method: "POST", body: ["by": "phone"])))
            },
            fill: { id in
                do { return try decode(FormFill.self, try await send(try request("postings/\(id)/fill"))) }
                catch APIError.http(404, _) { return nil }
            },
            submitGate: { id in
                try decode(SubmitGate.self, try await send(try request("postings/\(id)/approve-submit")))
            },
            approveSubmit: { id, note, fillDigest in
                // Only the Application preview approves a submit; the server refuses it from anywhere else.
                let body: [String: Any?] = ["by": "phone", "note": note, "from": "preview", "fill_digest": fillDigest]
                return try decode(Posting.self, try await send(try request("postings/\(id)/approve-submit", method: "POST", body: body)))
            },
            preview: { id in try decode(PreviewDoc.self, try await send(try request("postings/\(id)/preview"))) },
            review: { id in
                do { return try decode(ReviewDoc.self, try await send(try request("postings/\(id)/review"))) }
                catch APIError.http(404, _) { return nil }
            },
            approveBox: { id, key, seen in
                let body: [String: Any?] = ["by": "phone", "seen": seen]
                return try decode(ReviewDoc.self, try await send(try request("postings/\(id)/review/\(key)/approve", method: "POST", body: body)))
            },
            editBox: { id, key, text in
                let body: [String: Any?] = ["by": "phone", "text": text]
                return try decode(ReviewDoc.self, try await send(try request("postings/\(id)/review/\(key)/edit", method: "POST", body: body)))
            },
            commentBox: { id, key, text in
                let body: [String: Any?] = ["by": "phone", "text": text]
                return try decode(ReviewDoc.self, try await send(try request("postings/\(id)/review/\(key)/comment", method: "POST", body: body)))
            },
            fetchData: { rel in try await send(try request("files/" + rel)) },
            download: { rel in
                let data = try await send(try request("files/" + rel))
                let dir = FileManager.default.temporaryDirectory.appending(path: "materials", directoryHint: .isDirectory)
                try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                let dest = dir.appending(path: (rel as NSString).lastPathComponent)
                try data.write(to: dest, options: .atomic)
                return dest
            },
            fetchImage: { rel, maxPixel in
                let key = "\(maxPixel)|\(rel)"
                if let hit = await images.memory(key) { return hit }
                let disk = FileManager.default.temporaryDirectory
                    .appending(path: "images", directoryHint: .isDirectory)
                    .appending(path: rel.replacingOccurrences(of: "/", with: "__"))
                let data: Data
                if let cached = try? Data(contentsOf: disk) {
                    data = cached
                } else {
                    data = try await send(try request("files/" + rel))
                    try? FileManager.default.createDirectory(at: disk.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try? data.write(to: disk, options: .atomic)
                }
                guard let image = ImageCache.downsample(data, maxPixel: maxPixel) else {
                    throw APIError.decoding("not an image: \(rel)")
                }
                await images.store(image, for: key)
                return image
            },
            questions: { try decode([Question].self, try await send(try request("questions"))) },
            answerQuestion: { id, answer, note in
                let body: [String: Any?] = ["answer": answer, "note": note, "by": "phone"]
                return try decode(Question.self, try await send(try request("questions/\(id)/answer", method: "POST", body: body)))
            },
            dismissQuestion: { id in
                try decode(Question.self, try await send(try request("questions/\(id)/dismiss", method: "POST", body: ["by": "phone"])))
            },
            savedAnswers: { try decode([SavedAnswer].self, try await send(try request("answers"))) },
            changeSavedAnswer: { id, value in
                try decode(SavedAnswer.self, try await send(try request("answers/\(id)", method:"POST", body:["value":value,"by":"phone"])))
            },
            notifications: { try decode(NotificationStatus.self,try await send(try request("notifications"))) },
            setNotifications: { enabled in try decode(NotificationStatus.self,try await send(try request("notifications",method:"POST",body:["enabled":enabled]))) },
            roleSettings: { try decode(RoleSettings.self,try await send(try request("settings"))) },
            modelChoices: { try decode(ModelCatalog.self,try await send(try request("models"))) },
            saveRole: { role,choice in _ = try await send(try request("settings",method:"POST",body:["role":role,"choice":["agent":choice.agent,"model":choice.model]])) },
            chat: { since in
                let query = since > 0 ? [URLQueryItem(name: "since", value: String(since))] : []
                return try decode(ChatPage.self, try await send(try request("chat/page", query: query)))
            },
            sendChat: { text, postingId in
                let body: [String: Any?] = ["text": text, "by": "phone", "posting_id": postingId]
                return try decode(ChatMessage.self, try await send(try request("chat", method: "POST", body: body)))
            }
        )
    }
}

/// Decoded-image memory cache; NSCache evicts under memory pressure.
actor ImageCache {
    private let cache: NSCache<NSString, UIImage> = {
        let c = NSCache<NSString, UIImage>()
        c.totalCostLimit = 80 * 1024 * 1024
        return c
    }()

    func memory(_ key: String) -> UIImage? { cache.object(forKey: key as NSString) }

    func store(_ image: UIImage, for key: String) {
        let cost = image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
        cache.setObject(image, forKey: key as NSString, cost: cost)
    }

    /// Decodes straight to a thumbnail with ImageIO, so a 1600 px JPEG never sits in memory full-size.
    nonisolated static func downsample(_ data: Data, maxPixel: Int) -> UIImage? {
        guard let src = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary) else { return nil }
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
        ]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        return UIImage(cgImage: cg)
    }
}
