"use strict";
/**
 * Smoke tests, run inside VS Code's Extension Development Host.
 *
 * These verify the extension activates and its commands register correctly
 * — not that RAAG's metrics are correct, which is already covered by 307
 * tests in the CLI itself.
 */
Object.defineProperty(exports, "__esModule", { value: true });
const assert = require("assert");
const vscode = require("vscode");
suite("RAAG extension", () => {
    test("activates without throwing", async () => {
        const extension = vscode.extensions.getExtension("amankarki151.raag-vscode");
        assert.ok(extension, "extension should be discoverable");
        await extension?.activate();
        assert.strictEqual(extension?.isActive, true);
    });
    test("registers both commands", async () => {
        const commands = await vscode.commands.getCommands(true);
        assert.ok(commands.includes("raag.analyzeWorkspace"));
        assert.ok(commands.includes("raag.clearDecorations"));
    });
    test("clearDecorations runs without an active analysis", async () => {
        await vscode.commands.executeCommand("raag.clearDecorations");
    });
});
//# sourceMappingURL=extension.test.js.map