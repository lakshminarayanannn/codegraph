using System;
using MiniPlant.Core;
using NUnit.Framework;

namespace MiniPlant.Tests
{
    // Tests deliberately reach only part of the code: MotionController.MoveTo, StageBase.Move,
    // StageExtensions.MoveHome and Recipe.Validate are tested; InspectionService, PiezoStage and
    // the Calibrate chain are not.
    [TestFixture]
    public class MotionControllerTests
    {
        [Test]
        public void MoveTo_ForwardsToStage()
        {
            var stage = new LinearStage("T");
            new MotionController(stage).MoveTo(5.0);
            Assert.That(stage.ReadPosition(), Is.EqualTo(5.0));
        }

        [TestCase(0.0)]
        [TestCase(1.5)]
        public void MoveHome_ReturnsToZero(double start)
        {
            var stage = new LinearStage("T");
            stage.Move(start);
            stage.MoveHome();
            Assert.That(stage.ReadPosition(), Is.EqualTo(0.0));
        }
    }

    [TestFixture]
    public class RecipeTests
    {
        [Test]
        public void Validate_RejectsEmptyRecipe()
        {
            var recipe = new Recipe("empty");
            Assert.Throws<InvalidOperationException>(() => recipe.Validate());
        }
    }
}
