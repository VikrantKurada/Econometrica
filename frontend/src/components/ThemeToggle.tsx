import { Moon, Sun } from "lucide-react";

import { useThemeStore } from "../lib/theme";
import { IconButton } from "./ui/IconButton";

/**
 * Flips between light and dark. Until it is pressed the app follows the OS
 * preference; pressing it makes the choice explicit and persistent.
 */
export function ThemeToggle() {
  const resolved = useThemeStore((state) => state.resolved);
  const toggle = useThemeStore((state) => state.toggle);

  return (
    <IconButton
      label={resolved === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      icon={resolved === "dark" ? Sun : Moon}
      onClick={toggle}
    />
  );
}
