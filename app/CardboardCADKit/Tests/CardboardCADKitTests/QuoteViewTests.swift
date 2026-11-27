import XCTest
@testable import CardboardCADKit

// The Quote view (#47): its Issue quote button runs the engine's issue_quote tool,
// like the other design-tool buttons, instead of going to the app.

@MainActor
final class QuoteViewTests: XCTestCase {
    func testIssueQuoteIsADesignToolAction() async {
        XCTAssertTrue(ProjectViewsController.designTools.contains("issue_quote"))
        let c = ProjectViewsController()
        let fake = FakeProjectData()
        c.attach(client: fake)
        await c.run(action: "issue_quote")
        XCTAssertEqual(fake.actions, ["issue_quote"])
    }
}
