from urban_agent.intake import parse_requirement_text


def test_strategy_mapping_is_unchanged_when_scope_is_added():
    obj = parse_requirement_text("东部滨水地区改善慢行与活力")
    assert obj.strategy_keys == ["vitality", "street_friendly"]
    assert obj.dimension_weights == {"人和社会": 1.5, "道路与慢行": 1.5}
    assert obj.spatial_scope.mode == "intersection"
    assert obj.spatial_scope.terms == ("east", "waterfront")


def test_requirement_without_spatial_words_remains_compatible():
    obj = parse_requirement_text("打造零碳示范街区，同时提升街区活力")
    assert obj.strategy_keys == ["low_carbon", "vitality"]
    assert obj.spatial_scope is None


def test_nested_scope_is_attached_to_objectives():
    obj = parse_requirement_text("调整中部地区偏西部分的住宅布局")
    assert obj.spatial_scope.mode == "nested"
    assert obj.spatial_scope.terms == ("middle", "west")
