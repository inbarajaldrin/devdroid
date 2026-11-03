import Foundation

/// Published updates are offered only after comparing the running binary's own build.
struct AppUpdate: Equatable {
    let build: Int
    let link: URL

    static func available(build: String?, install: String?, ownBuild: Int, dismissedBuild: Int) -> AppUpdate? {
        guard let build, let number = Int(build), number > ownBuild, number != dismissedBuild,
              let install, let link = URL(string: install), link.scheme == "itms-services",
              let query = URLComponents(url:link,resolvingAgainstBaseURL:false)?.queryItems,
              query.first(where:{$0.name == "action"})?.value == "download-manifest",
              let manifest = query.first(where:{$0.name == "url"})?.value.flatMap({URL(string:$0)}),
              manifest.scheme == "https", manifest.host != nil, manifest.user == nil, manifest.password == nil
        else { return nil }
        return AppUpdate(build:number,link:link)
    }
}
