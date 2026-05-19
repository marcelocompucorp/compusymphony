defmodule SymphonyElixirWeb.DashboardLiveHelpersTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixirWeb.DashboardLive

  describe "pip_class/3" do
    test "returns pip-done for steps before current" do
      assert DashboardLive.pip_class_for_test(1, 3, 5) == "pip pip-done"
      assert DashboardLive.pip_class_for_test(2, 3, 5) == "pip pip-done"
    end

    test "returns pip-active for the current step" do
      assert DashboardLive.pip_class_for_test(3, 3, 5) == "pip pip-active"
    end

    test "returns pip-empty for steps after current" do
      assert DashboardLive.pip_class_for_test(4, 3, 5) == "pip pip-empty"
      assert DashboardLive.pip_class_for_test(5, 3, 5) == "pip pip-empty"
    end

    test "first step: only step 1 is active, rest are empty" do
      assert DashboardLive.pip_class_for_test(1, 1, 4) == "pip pip-active"
      assert DashboardLive.pip_class_for_test(2, 1, 4) == "pip pip-empty"
    end

    test "last step: all previous are done, last is active" do
      assert DashboardLive.pip_class_for_test(3, 4, 4) == "pip pip-done"
      assert DashboardLive.pip_class_for_test(4, 4, 4) == "pip pip-active"
    end
  end

  describe "priority_label/1" do
    test "maps integers 1-4 to correct labels" do
      assert DashboardLive.priority_label_for_test(1) == "Urgent"
      assert DashboardLive.priority_label_for_test(2) == "High"
      assert DashboardLive.priority_label_for_test(3) == "Medium"
      assert DashboardLive.priority_label_for_test(4) == "Low"
    end

    test "returns nil for nil priority" do
      assert DashboardLive.priority_label_for_test(nil) == nil
    end

    test "returns nil for out-of-range integers" do
      assert DashboardLive.priority_label_for_test(0) == nil
      assert DashboardLive.priority_label_for_test(5) == nil
    end
  end

  describe "priority_badge_class/1" do
    test "maps integers 1-4 to correct CSS classes" do
      assert DashboardLive.priority_badge_class_for_test(1) == "priority-badge priority-urgent"
      assert DashboardLive.priority_badge_class_for_test(2) == "priority-badge priority-high"
      assert DashboardLive.priority_badge_class_for_test(3) == "priority-badge priority-medium"
      assert DashboardLive.priority_badge_class_for_test(4) == "priority-badge priority-low"
    end

    test "returns nil for nil and out-of-range" do
      assert DashboardLive.priority_badge_class_for_test(nil) == nil
      assert DashboardLive.priority_badge_class_for_test(0) == nil
    end
  end

  describe "truncate_title/1" do
    test "returns em-dash for nil" do
      assert DashboardLive.truncate_title_for_test(nil) == "—"
    end

    test "returns title unchanged when 60 bytes or fewer" do
      short = String.duplicate("a", 60)
      assert DashboardLive.truncate_title_for_test(short) == short
    end

    test "truncates to 57 chars + ellipsis when over 60 bytes" do
      long = String.duplicate("a", 80)
      result = DashboardLive.truncate_title_for_test(long)
      assert String.ends_with?(result, "…")
      # 57 ASCII chars + 3-byte UTF-8 ellipsis
      assert byte_size(result) <= 61
    end

    test "does not truncate a 60-char title" do
      exact = String.duplicate("b", 60)
      assert DashboardLive.truncate_title_for_test(exact) == exact
    end
  end
end
