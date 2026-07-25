import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * Phase 1 smoke test: the full stack — browser, Vite proxy, FastAPI,
 * Postgres — exercised through the one workflow the skeleton supports.
 *
 * Runs against the real dev database, so every name carries a per-run
 * timestamp (existing rows can never cause a false failure) and the project
 * is deleted through the API afterwards (repeated runs leave no junk behind).
 */

const stamp = Date.now();
const projectName = `E2E project ${stamp}`;
const projectRenamed = `E2E project ${stamp} renamed`;
const chatName = `E2E chat ${stamp}`;
const chatRenamed = `E2E chat ${stamp} renamed`;

/** Delete every project this run created, whatever state the test died in. */
async function cleanUp(request: APIRequestContext): Promise<void> {
  const response = await request.get("/api/projects");
  if (!response.ok()) return;
  const projects = (await response.json()) as { id: string; name: string }[];
  for (const project of projects) {
    if (project.name.includes(String(stamp))) {
      await request.delete(`/api/projects/${project.id}`);
    }
  }
}

test.afterEach(async ({ request }) => {
  await cleanUp(request);
});

test("project and chat lifecycle survives a reload", async ({ page }) => {
  await page.goto("/");

  // Create a project. The app drops straight into inline naming, so the
  // create and the first name are one gesture.
  await page.getByRole("button", { name: "New project" }).click();
  const nameInput = page.getByRole("textbox", { name: /rename project/i });
  await nameInput.fill(projectName);
  await nameInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: projectName })).toBeVisible();

  // Rename it: double-click opens the inline editor, Enter commits.
  await page.getByText(projectName, { exact: true }).dblclick();
  const projectRenameInput = page.getByRole("textbox", { name: /rename project/i });
  await projectRenameInput.fill(projectRenamed);
  await projectRenameInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: projectRenamed })).toBeVisible();

  // Add a chat under it. The add-chat control lives in the row's hover
  // actions; creation again drops straight into inline naming.
  await page.getByText(projectRenamed, { exact: true }).hover();
  await page.getByRole("button", { name: `New chat in ${projectRenamed}` }).click();
  const chatInput = page.getByRole("textbox", { name: /rename chat/i });
  await chatInput.fill(chatName);
  await chatInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: chatName })).toBeVisible();

  // Rename the chat the same way.
  await page.getByText(chatName, { exact: true }).dblclick();
  const chatRenameInput = page.getByRole("textbox", { name: /rename chat/i });
  await chatRenameInput.fill(chatRenamed);
  await chatRenameInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: chatRenamed })).toBeVisible();

  // Collapse before reloading. Expansion state persists in localStorage, so
  // without this the post-reload chat list could be answered by a rehydrated
  // UI; collapsing forces the re-expand to fetch chats from the API afresh.
  await page.getByRole("button", { name: `Collapse ${projectRenamed}` }).click();
  await expect(page.getByRole("treeitem", { name: chatRenamed })).toBeHidden();

  // The point of the exercise: both renames must have reached Postgres, not
  // just the client cache.
  await page.reload();

  await expect(page.getByRole("treeitem", { name: projectRenamed })).toBeVisible();
  await page.getByRole("button", { name: `Expand ${projectRenamed}` }).click();
  await expect(page.getByRole("treeitem", { name: chatRenamed })).toBeVisible();
});
