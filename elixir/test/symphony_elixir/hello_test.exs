defmodule SymphonyElixir.HelloTest do
  use SymphonyElixir.TestSupport

  test "SymphonyElixir module is loaded" do
    assert Code.ensure_loaded?(SymphonyElixir)
  end

  test "Application module defines start/2 callback" do
    assert function_exported?(SymphonyElixir.Application, :start, 2)
  end
end
